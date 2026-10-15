"""Unit tests for ThreatAssessNode (rule evaluation → ThreatPackets)."""
from __future__ import annotations

import asyncio

import pytest

from probable_intel.nexus.spec import EmitSpec, NodeSpec, RuleSpec
from probable_intel.nodes.analysts.threat_node import ThreatAssessNode
from probable_intel.spine.packet import IntelPacket, Priority
from probable_intel.spine.spine import Spine


def _make_node(spine: Spine, *rules: tuple[str, str, str]) -> ThreatAssessNode:
    spec = NodeSpec(
        node_type="ThreatAssessNode",
        node_id="analyst.threat.test",
        apparatus_id="test",
        subscribe_channels=["analysis.sentiment.test"],
        emit=EmitSpec(channel="threat.test", priority="high"),
        rules=[RuleSpec(condition=c, severity=s, label=lbl) for c, s, lbl in rules],
    )
    return ThreatAssessNode(spec, spine)


def _packet(**payload) -> IntelPacket:
    return IntelPacket(
        packet_type="SentimentPacket",
        source_node_id="analyst.sentiment.test",
        apparatus_id="test",
        channel="analysis.sentiment.test",
        payload=payload,
    )


async def _next(sub, timeout: float = 0.5):
    return await asyncio.wait_for(sub.get(), timeout)


async def test_matching_rules_emit_threat_with_highest_severity():
    spine = Spine()
    node = _make_node(
        spine,
        ("sentiment_score < -0.5", "HIGH", "negative"),
        ("sentiment_score < -0.7", "CRITICAL", "very-negative"),
    )
    await node.setup()
    sub = spine.subscribe("threat.test")

    await node._assess(_packet(sentiment_score=-0.9, title="Ransomware"))

    threat = await _next(sub)
    assert threat.packet_type == "ThreatPacket"
    assert threat.payload["max_severity"] == "CRITICAL"
    assert threat.payload["rule_count"] == 2
    assert {m["label"] for m in threat.payload["threat_matches"]} == {"negative", "very-negative"}
    assert threat.payload["title"] == "Ransomware"  # input payload is carried through
    assert threat.priority == Priority.CRITICAL
    assert threat.provenance[0] == "analyst.threat.test"
    await node.teardown()


async def test_severity_uses_rank_not_alphabetical_order():
    spine = Spine()
    node = _make_node(
        spine,
        ("score > 0", "MEDIUM", "medium-rule"),
        ("score > 0", "HIGH", "high-rule"),
    )
    await node.setup()
    sub = spine.subscribe("threat.test")

    await node._assess(_packet(score=1))

    assert (await _next(sub)).payload["max_severity"] == "HIGH"  # "MEDIUM" > "HIGH" as strings
    await node.teardown()


async def test_no_matching_rule_emits_nothing():
    spine = Spine()
    node = _make_node(spine, ("sentiment_score < -0.5", "HIGH", "negative"))
    await node.setup()
    sub = spine.subscribe("threat.test")

    await node._assess(_packet(sentiment_score=0.4))

    with pytest.raises(asyncio.TimeoutError):
        await _next(sub, timeout=0.1)
    await node.teardown()


async def test_contains_rule_on_tags():
    spine = Spine()
    node = _make_node(spine, ('tags contains "ransomware"', "HIGH", "ransomware-tag"))
    await node.setup()
    sub = spine.subscribe("threat.test")

    await node._assess(_packet(tags=["breach", "ransomware"]))

    assert (await _next(sub)).payload["threat_matches"][0]["label"] == "ransomware-tag"
    await node.teardown()
