"""Unit tests for EntityExtractorNode (spaCy, degraded, and LLM-fallback paths)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from probable_intel.nexus.spec import EmitSpec, NodeSpec
from probable_intel.nodes.analysts.entity_node import EntityExtractorNode
from probable_intel.spine.packet import IntelPacket
from probable_intel.spine.spine import Spine


def _make_node(spine: Spine) -> EntityExtractorNode:
    spec = NodeSpec(
        node_type="EntityExtractorNode",
        node_id="analyst.entities.test",
        apparatus_id="test",
        subscribe_channels=["raw.feed.test"],
        emit=EmitSpec(channel="analysis.entities.test", priority="normal"),
    )
    return EntityExtractorNode(spec, spine)


def _packet(**payload) -> IntelPacket:
    return IntelPacket(
        packet_type="RawFeedPacket",
        source_node_id="feed.test",
        apparatus_id="test",
        channel="raw.feed.test",
        payload=payload,
    )


def _fake_nlp(*ents: tuple[str, str]):
    """Stand-in for a spaCy pipeline: returns a doc whose .ents are (text, label)."""
    def nlp(text: str):
        found = []
        for word, label in ents:
            start = text.find(word)
            if start >= 0:
                found.append(SimpleNamespace(
                    text=word, label_=label, start_char=start, end_char=start + len(word),
                ))
        return SimpleNamespace(ents=found)
    return nlp


async def _next(sub, timeout: float = 0.5):
    return await asyncio.wait_for(sub.get(), timeout)


async def test_spacy_entities_are_emitted_with_offsets():
    spine = Spine()
    node = _make_node(spine)
    await node.setup()
    node._nlp = _fake_nlp(("APT28", "ORG"), ("Ukraine", "GPE"))
    sub = spine.subscribe("analysis.entities.test")

    await node._process(_packet(content="APT28 targeted Ukraine again.", title="t"))

    out = await _next(sub)
    assert out.packet_type == "EntityPacket"
    assert out.payload["entities"] == [
        {"text": "APT28", "type": "ORG", "start": 0, "end": 5},
        {"text": "Ukraine", "type": "GPE", "start": 15, "end": 22},
    ]
    assert out.payload["entity_count"] == 2
    assert out.payload["title"] == "t"
    await node.teardown()


async def test_spacy_path_skips_packets_without_entities():
    spine = Spine()
    node = _make_node(spine)
    await node.setup()
    node._nlp = _fake_nlp(("APT28", "ORG"))
    sub = spine.subscribe("analysis.entities.test")

    await node._process(_packet(content="Nothing notable here."))

    with pytest.raises(asyncio.TimeoutError):
        await _next(sub, timeout=0.1)
    await node.teardown()


async def test_without_spacy_or_llm_packets_pass_through_with_no_entities():
    spine = Spine()
    node = _make_node(spine)
    await node.setup()
    node._nlp = None
    sub = spine.subscribe("analysis.entities.test")

    await node._process(_packet(content="APT28 targeted Ukraine again."))
    await node._process(_packet(title="no body"))  # no content → ignored

    out = await _next(sub)
    assert out.payload["entities"] == [] and out.payload["entity_count"] == 0
    with pytest.raises(asyncio.TimeoutError):
        await _next(sub, timeout=0.1)
    await node.teardown()


async def test_llm_fallback_output_is_normalized():
    spine = Spine()
    node = _make_node(spine)
    await node.setup()
    node._nlp = None
    node._llm_router = SimpleNamespace(complete=AsyncMock(return_value=(
        'Sure! Here are the entities:\n'
        '[{"text": "APT28", "type": "ORG"}, "CVE-2024-1234", {"type": "GPE"}, 42, '
        '{"text": "  ", "type": "ORG"}]'
    )))
    sub = spine.subscribe("analysis.entities.test")

    await node._process(_packet(content="APT28 exploited CVE-2024-1234."))

    out = await _next(sub)
    assert out.payload["entities"] == [
        {"text": "APT28", "type": "ORG"},
        {"text": "CVE-2024-1234", "type": "UNKNOWN"},
    ]
    assert "llm-entities" in out.tags
    await node.teardown()


async def test_llm_fallback_failure_still_emits_empty_entities():
    spine = Spine()
    node = _make_node(spine)
    await node.setup()
    node._nlp = None
    node._llm_router = SimpleNamespace(complete=AsyncMock(side_effect=RuntimeError("quota")))
    sub = spine.subscribe("analysis.entities.test")

    await node._process(_packet(content="APT28 exploited CVE-2024-1234."))

    out = await _next(sub)
    assert out.payload["entities"] == []
    assert "llm-entities" not in out.tags
    await node.teardown()
