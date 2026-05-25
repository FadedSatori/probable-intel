"""Unit tests for TaskRouterNode."""
from __future__ import annotations

import asyncio
import pytest

from probable_intel.nexus.spec import NodeSpec, EmitSpec
from probable_intel.spine.spine import Spine
from probable_intel.spine.packet import IntelPacket, Priority
from probable_intel.nodes.coordinators.task_router import TaskRouterNode


def _make_spec(min_severity="HIGH", cooldown=0, max_per_hour=100) -> NodeSpec:
    return NodeSpec(
        node_type="TaskRouterNode",
        node_id="coordinator.task-router.test",
        apparatus_id="test",
        subscribe_channels=["threat.test", "analysis.anomaly"],
        emit=EmitSpec(channel="system.task.directives", priority="high"),
        config={
            "min_severity": min_severity,
            "directive_cooldown_seconds": cooldown,
            "max_directives_per_hour": max_per_hour,
        },
    )


def _threat_packet(severity: str = "HIGH") -> IntelPacket:
    return IntelPacket(
        packet_type="ThreatPacket",
        source_node_id="analyst.threat.test",
        apparatus_id="test",
        channel="threat.test",
        payload={
            "severity": severity,
            "matched_rules": [{"label": "ransomware-detected", "severity": severity}],
            "entities": [{"text": "Lazarus", "type": "ORG"}],
        },
        priority=Priority.HIGH,
    )


def _anomaly_packet(severity: str = "HIGH") -> IntelPacket:
    return IntelPacket(
        packet_type="AnomalyPacket",
        source_node_id="anomaly.test",
        apparatus_id="test",
        channel="analysis.anomaly",
        payload={
            "max_severity": severity,
            "metric": "volume_z_score",
            "current_value": 4.5,
            "channel": "raw.feed.test",
        },
        priority=Priority.HIGH,
    )


# ── Severity filter ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_below_min_severity_ignored():
    """LOW severity packets don't produce directives."""
    spine = Spine()
    node = TaskRouterNode(_make_spec(min_severity="HIGH"), spine)
    await node.setup()

    sub = spine.subscribe("system.task.directives")
    await spine.publish("threat.test", _threat_packet(severity="LOW"))
    await node.run()

    assert sub._queue.empty()
    sub.close()
    await node.stop()


@pytest.mark.asyncio
async def test_high_severity_emits_directive():
    """HIGH severity threat packet → TaskDirectivePacket emitted."""
    spine = Spine()
    node = TaskRouterNode(_make_spec(), spine)
    await node.setup()

    sub = spine.subscribe("system.task.directives")
    await spine.publish("threat.test", _threat_packet(severity="HIGH"))
    await node.run()

    result = await asyncio.wait_for(sub.get(), timeout=2.0)
    assert result.packet_type == "TaskDirectivePacket"
    p = result.payload
    assert "directive_type" in p
    assert "target_node_id" in p
    assert "rationale" in p
    assert "ttl_seconds" in p
    sub.close()
    await node.stop()


@pytest.mark.asyncio
async def test_critical_emits_directive():
    """CRITICAL severity always triggers (above HIGH threshold)."""
    spine = Spine()
    node = TaskRouterNode(_make_spec(min_severity="HIGH"), spine)
    await node.setup()

    sub = spine.subscribe("system.task.directives")
    await spine.publish("threat.test", _threat_packet(severity="CRITICAL"))
    await node.run()

    result = await asyncio.wait_for(sub.get(), timeout=2.0)
    assert result.packet_type == "TaskDirectivePacket"
    assert result.payload["severity"] == "CRITICAL"
    sub.close()
    await node.stop()


# ── Cooldown ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cooldown_suppresses_repeat():
    """Two HIGH packets for the same topic within cooldown → only one directive."""
    spine = Spine()
    node = TaskRouterNode(_make_spec(cooldown=9999), spine)
    await node.setup()

    sub = spine.subscribe("system.task.directives")

    for _ in range(2):
        await spine.publish("threat.test", _threat_packet(severity="HIGH"))
        await node.run()

    # Only one directive should be in the queue
    await asyncio.wait_for(sub.get(), timeout=2.0)
    assert sub._queue.empty()
    sub.close()
    await node.stop()


# ── Rate limit ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rate_limit_caps_directives():
    """max_directives_per_hour prevents excess emissions."""
    spine = Spine()
    node = TaskRouterNode(_make_spec(cooldown=0, max_per_hour=2), spine)
    await node.setup()

    sub = spine.subscribe("system.task.directives")

    for i in range(5):
        pkt = IntelPacket(
            packet_type="ThreatPacket",
            source_node_id="test",
            apparatus_id="test",
            channel="threat.test",
            payload={"severity": "HIGH", "matched_rules": [{"label": f"rule-{i}"}], "entities": []},
            priority=Priority.HIGH,
        )
        await spine.publish("threat.test", pkt)
        await node.run()

    count = 0
    while not sub._queue.empty():
        sub._queue.get_nowait()
        count += 1
    assert count <= 2
    sub.close()
    await node.stop()


# ── Directive content ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_directive_has_required_fields():
    """TaskDirectivePacket payload has all required keys."""
    spine = Spine()
    node = TaskRouterNode(_make_spec(), spine)
    await node.setup()

    sub = spine.subscribe("system.task.directives")
    await spine.publish("analysis.anomaly", _anomaly_packet())
    await node.run()

    result = await asyncio.wait_for(sub.get(), timeout=2.0)
    p = result.payload
    for key in ("directive_type", "target_node_id", "parameters", "rationale", "ttl_seconds", "source_signal"):
        assert key in p, f"missing key: {key}"
    sub.close()
    await node.stop()


@pytest.mark.asyncio
async def test_rule_based_fallback_no_llm():
    """Without LLM, rule-based directive is still emitted."""
    spine = Spine()
    spec = _make_spec()
    spec.llm = None  # ensure no LLM
    node = TaskRouterNode(spec, spine)
    await node.setup()

    sub = spine.subscribe("system.task.directives")
    await spine.publish("threat.test", _threat_packet())
    await node.run()

    result = await asyncio.wait_for(sub.get(), timeout=2.0)
    assert result.packet_type == "TaskDirectivePacket"
    assert result.payload["directive_type"] in (
        "add_keyword_filter", "expand_collection", "pause_channel", "deprioritize"
    )
    sub.close()
    await node.stop()
