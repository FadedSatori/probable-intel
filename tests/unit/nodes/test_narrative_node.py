"""Unit tests for NarrativeNode."""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from probable_intel.nexus.spec import NodeSpec, EmitSpec, LLMSpec
from probable_intel.spine.spine import Spine
from probable_intel.spine.packet import IntelPacket, Priority
from probable_intel.nodes.analysts.narrative_node import NarrativeNode


def _make_spec(window=3, interval=9999, with_llm=True) -> NodeSpec:
    return NodeSpec(
        node_type="NarrativeNode",
        node_id="narrative.test",
        apparatus_id="test",
        subscribe_channels=["analysis.sentiment.test"],
        emit=EmitSpec(channel="analysis.narrative.test", priority="normal"),
        config={"narrative_window": window, "emit_interval_seconds": interval},
        llm=LLMSpec(provider="anthropic", model="claude-haiku-4-5-20251001") if with_llm else None,
    )


def _packet(content: str = "threat intel content") -> IntelPacket:
    return IntelPacket(
        packet_type="SentimentPacket",
        source_node_id="analyst.sentiment",
        apparatus_id="test",
        channel="analysis.sentiment.test",
        payload={"content": content, "sentiment_score": -0.5},
        priority=Priority.NORMAL,
    )


def _mock_router(response: str = "Threat actor X exploiting CVE-Y.") -> MagicMock:
    router = MagicMock()
    router.complete = AsyncMock(return_value=response)
    return router


# ── setup ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_narrative_requires_llm_config():
    """Without spec.llm, NarrativeNode produces no output."""
    spine = Spine()
    node = NarrativeNode(_make_spec(with_llm=False, interval=0), spine)
    await node.setup()

    sub = spine.subscribe("analysis.narrative.test")
    for _ in range(5):
        await spine.publish("analysis.sentiment.test", _packet())
        await node.run()

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(sub.get(), timeout=0.1)
    sub.close()
    await node.stop()


@pytest.mark.asyncio
async def test_narrative_accumulates_window():
    """Fewer packets than narrative_window → no emit yet."""
    spine = Spine()
    node = NarrativeNode(_make_spec(window=5, interval=9999), spine)
    await node.setup()
    node._llm_router = _mock_router()

    sub = spine.subscribe("analysis.narrative.test")
    for _ in range(3):
        await spine.publish("analysis.sentiment.test", _packet())
        await node.run()

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(sub.get(), timeout=0.1)
    sub.close()
    await node.stop()


@pytest.mark.asyncio
async def test_narrative_emits_when_window_full():
    """Exactly narrative_window packets triggers a NarrativePacket emit."""
    spine = Spine()
    node = NarrativeNode(_make_spec(window=3, interval=9999), spine)
    await node.setup()
    node._llm_router = _mock_router("Threat actor X exploiting CVE-Y in healthcare sector.")

    sub = spine.subscribe("analysis.narrative.test")
    for _ in range(3):
        await spine.publish("analysis.sentiment.test", _packet())
        await node.run()

    result = await asyncio.wait_for(sub.get(), timeout=2.0)
    assert result.packet_type == "NarrativePacket"
    assert result.payload["source_count"] == 3
    assert "Threat actor" in result.payload["summary"]
    assert result.payload["window_start"] != ""
    assert result.payload["window_end"] != ""
    sub.close()
    await node.stop()


@pytest.mark.asyncio
async def test_narrative_emits_on_interval():
    """emit_interval_seconds=0 forces emit even with a single packet."""
    spine = Spine()
    node = NarrativeNode(_make_spec(window=100, interval=0), spine)
    await node.setup()
    node._llm_router = _mock_router("Single signal summary.")

    sub = spine.subscribe("analysis.narrative.test")
    await spine.publish("analysis.sentiment.test", _packet())
    await node.run()

    result = await asyncio.wait_for(sub.get(), timeout=2.0)
    assert result.packet_type == "NarrativePacket"
    assert result.payload["source_count"] == 1
    sub.close()
    await node.stop()


@pytest.mark.asyncio
async def test_narrative_clears_window_after_emit():
    """After emitting, the window resets — next packets start fresh."""
    spine = Spine()
    node = NarrativeNode(_make_spec(window=2, interval=9999), spine)
    await node.setup()
    node._llm_router = _mock_router("summary")

    sub = spine.subscribe("analysis.narrative.test")

    # First window — fills and emits
    for _ in range(2):
        await spine.publish("analysis.sentiment.test", _packet())
        await node.run()
    await asyncio.wait_for(sub.get(), timeout=2.0)

    # Window should be cleared — one more packet should not trigger emit
    await spine.publish("analysis.sentiment.test", _packet())
    await node.run()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(sub.get(), timeout=0.1)

    sub.close()
    await node.stop()


@pytest.mark.asyncio
async def test_narrative_llm_failure_no_crash():
    """LLM exception is caught — node logs warning and does not emit."""
    spine = Spine()
    node = NarrativeNode(_make_spec(window=2, interval=9999), spine)
    await node.setup()
    failing_router = MagicMock()
    failing_router.complete = AsyncMock(side_effect=RuntimeError("LLM unavailable"))
    node._llm_router = failing_router

    sub = spine.subscribe("analysis.narrative.test")
    for _ in range(2):
        await spine.publish("analysis.sentiment.test", _packet())
        await node.run()

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(sub.get(), timeout=0.1)
    sub.close()
    await node.stop()
