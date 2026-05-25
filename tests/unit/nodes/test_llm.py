"""Unit tests for LLM layer — providers, LLMRouter budget guard, node integration."""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from probable_intel.nexus.spec import LLMSpec, NodeSpec, EmitSpec
from probable_intel.spine.spine import Spine
from probable_intel.spine.packet import IntelPacket, Priority
from probable_intel.llm.router import LLMRouter, LLMBudgetError
from probable_intel.llm.base import LLMError, LLMProvider
from probable_intel.llm.anthropic_provider import AnthropicProvider
from probable_intel.llm.ollama_provider import OllamaProvider
from probable_intel.llm.vllm_provider import VLLMProvider


def _make_llm_spec(budget=5.0, model="claude-haiku-4-5-20251001",
                   provider="anthropic", base_url="") -> LLMSpec:
    return LLMSpec(provider=provider, model=model,
                   api_key_env="ANTHROPIC_API_KEY", base_url=base_url,
                   max_tokens=4096, budget_per_day_usd=budget)


def _mock_provider(response: str = "0.5") -> AnthropicProvider:
    provider = MagicMock(spec=AnthropicProvider)
    provider.complete = AsyncMock(return_value=response)
    return provider


# ── Provider abstraction ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ollama_provider_posts_to_api_chat(respx_mock):
    """OllamaProvider POSTs to /api/chat and returns message.content."""
    import respx, httpx
    respx_mock.post("http://localhost:11434/api/chat").mock(
        return_value=httpx.Response(200, json={"message": {"content": "ollama says hi"}})
    )
    spec = _make_llm_spec(provider="ollama", model="llama3.2", budget=0.0)
    provider = OllamaProvider(spec)
    result = await provider.complete("hello", system="you are helpful")
    assert result == "ollama says hi"


@pytest.mark.asyncio
async def test_vllm_provider_posts_to_chat_completions(respx_mock):
    """VLLMProvider POSTs to /v1/chat/completions and returns choices content."""
    import respx, httpx
    respx_mock.post("http://localhost:8000/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={
            "choices": [{"message": {"content": "vllm says hi"}}]
        })
    )
    spec = _make_llm_spec(provider="vllm", model="mistral-7b", budget=0.0,
                          base_url="http://localhost:8000")
    provider = VLLMProvider(spec)
    result = await provider.complete("hello")
    assert result == "vllm says hi"


def test_router_from_spec_selects_ollama():
    """LLMRouter.from_spec dispatches to OllamaProvider when provider='ollama'."""
    spec = _make_llm_spec(provider="ollama", model="llama3.2", budget=0.0)
    router = LLMRouter.from_spec(spec)
    assert isinstance(router._provider, OllamaProvider)


def test_router_from_spec_selects_vllm():
    """LLMRouter.from_spec dispatches to VLLMProvider when provider='vllm'."""
    spec = _make_llm_spec(provider="vllm", model="mistral-7b", budget=0.0)
    router = LLMRouter.from_spec(spec)
    assert isinstance(router._provider, VLLMProvider)


@pytest.mark.asyncio
async def test_router_skips_budget_for_free_providers():
    """budget_per_day_usd=0.0 disables the budget guard entirely."""
    spec = _make_llm_spec(provider="ollama", budget=0.0)
    mock = _mock_provider("response")
    router = LLMRouter(mock, spec)
    # 1000 calls should never raise LLMBudgetError
    for _ in range(50):
        await router.complete("x" * 1000, max_tokens=512)
    assert router.usd_today == 0.0  # not tracked for free providers


def test_router_unknown_provider_raises():
    """LLMError raised for unrecognised provider name."""
    spec = _make_llm_spec(provider="openai")
    with pytest.raises(LLMError, match="unknown LLM provider"):
        LLMRouter.from_spec(spec)


# ── LLMRouter budget guard ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_router_budget_guard():
    """LLMBudgetError raised when next call would exceed daily budget."""
    spec = _make_llm_spec(budget=0.0001)  # tiny budget
    router = LLMRouter(_mock_provider(), spec)

    # First call should exhaust the budget
    try:
        await router.complete("hello", max_tokens=1024)
    except LLMBudgetError:
        pass  # expected on first if budget is tiny enough

    # Manually set spent to near-budget
    router._usd_today = 0.00009
    with pytest.raises(LLMBudgetError):
        await router.complete("hello world " * 100, max_tokens=1024)


@pytest.mark.asyncio
async def test_router_calls_provider():
    """Router delegates to provider and returns its response."""
    provider = _mock_provider("the answer")
    spec = _make_llm_spec(budget=10.0)
    router = LLMRouter(provider, spec)
    result = await router.complete("question?", max_tokens=50)
    assert result == "the answer"
    provider.complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_router_tracks_spend():
    """usd_today increments after each call."""
    spec = _make_llm_spec(budget=10.0)
    router = LLMRouter(_mock_provider(), spec)
    assert router.usd_today == 0.0
    await router.complete("x", max_tokens=100)
    assert router.usd_today > 0.0


@pytest.mark.asyncio
async def test_router_resets_on_new_day():
    """Spend counter resets when the calendar day changes."""
    from datetime import date
    spec = _make_llm_spec(budget=10.0)
    router = LLMRouter(_mock_provider(), spec)
    router._usd_today = 4.99
    # Simulate yesterday
    router._day = date(2000, 1, 1)
    assert router.usd_today == 0.0  # property triggers refresh


@pytest.mark.asyncio
async def test_budget_remaining():
    """budget_remaining reflects what's left."""
    spec = _make_llm_spec(budget=5.0)
    router = LLMRouter(_mock_provider(), spec)
    router._usd_today = 2.0
    assert abs(router.budget_remaining - 3.0) < 0.01


# ── SentimentNode LLM fallback ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sentiment_uses_llm_when_low_confidence():
    """SentimentNode calls LLM when VADER confidence < threshold."""
    from probable_intel.nodes.analysts.sentiment_node import SentimentNode

    spec = NodeSpec(
        node_type="SentimentNode",
        node_id="sentiment.test",
        apparatus_id="test",
        subscribe_channels=["raw.test"],
        emit=EmitSpec(channel="analysis.sentiment.test", priority="normal"),
        backend={"primary": "vader", "llm_threshold": "0.9"},  # very high threshold → always use LLM
        llm=_make_llm_spec(),
    )

    spine = Spine()
    node = SentimentNode(spec, spine)

    # Mock the LLM router
    mock_router = MagicMock()
    mock_router.complete = AsyncMock(return_value="-0.8")
    node._llm_router = mock_router

    await node.setup()
    node._llm_router = mock_router  # re-set after setup overwrites

    sub = spine.subscribe("analysis.sentiment.test")

    packet = IntelPacket(
        packet_type="RawPacket",
        source_node_id="test",
        apparatus_id="test",
        channel="raw.test",
        payload={"content": "This article discusses a potential vulnerability."},
        priority=Priority.NORMAL,
    )
    await spine.publish("raw.test", packet)
    await node.run()

    result = await asyncio.wait_for(sub.get(), timeout=2.0)
    assert result.payload["sentiment_backend"] == "llm"
    assert abs(result.payload["sentiment_score"] - (-0.8)) < 0.01
    assert "llm-sentiment" in result.tags
    sub.close()
    await node.stop()


# ── EntityExtractorNode LLM fallback ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_entity_uses_llm_when_spacy_unavailable():
    """EntityExtractorNode uses LLM when spaCy model is absent."""
    from probable_intel.nodes.analysts.entity_node import EntityExtractorNode

    spec = NodeSpec(
        node_type="EntityExtractorNode",
        node_id="entity.test",
        apparatus_id="test",
        subscribe_channels=["raw.test"],
        emit=EmitSpec(channel="analysis.entities.test", priority="normal"),
        llm=_make_llm_spec(),
    )

    spine = Spine()
    node = EntityExtractorNode(spec, spine)

    llm_response = '[{"text": "CVE-2024-001", "type": "CVE"}, {"text": "Acme Corp", "type": "ORG"}]'
    mock_router = MagicMock()
    mock_router.complete = AsyncMock(return_value=llm_response)
    node._nlp = None  # force no-spaCy path
    node._llm_router = mock_router

    await node.setup()
    node._nlp = None
    node._llm_router = mock_router

    sub = spine.subscribe("analysis.entities.test")

    packet = IntelPacket(
        packet_type="RawPacket",
        source_node_id="test",
        apparatus_id="test",
        channel="raw.test",
        payload={"content": "CVE-2024-001 affects Acme Corp systems."},
        priority=Priority.NORMAL,
    )
    await spine.publish("raw.test", packet)
    await node.run()

    result = await asyncio.wait_for(sub.get(), timeout=2.0)
    assert result.payload["entity_count"] == 2
    texts = {e["text"] for e in result.payload["entities"]}
    assert "CVE-2024-001" in texts
    assert "llm-entities" in result.tags
    sub.close()
    await node.stop()
