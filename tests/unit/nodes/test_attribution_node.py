"""Unit tests for AttributionNode."""
from __future__ import annotations

import asyncio
import time
import pytest

from probable_intel.nexus.spec import NodeSpec, EmitSpec
from probable_intel.spine.spine import Spine
from probable_intel.spine.packet import IntelPacket, Priority
from probable_intel.nodes.counterintel.attribution_node import AttributionNode


def _make_spec(min_hits=2, session_window=3600) -> NodeSpec:
    return NodeSpec(
        node_type="AttributionNode",
        node_id="ci.attribution.test",
        apparatus_id="test",
        subscribe_channels=["ci.deception.triggers"],
        emit=EmitSpec(channel="ci.attribution.events", priority="high"),
        config={
            "session_window_seconds": session_window,
            "min_hits_to_profile": min_hits,
        },
    )


def _trigger_packet(ip: str, canary_id: str = "canary-01", path: str = "/api/v1/test") -> IntelPacket:
    return IntelPacket(
        packet_type="DeceptionTriggerPacket",
        source_node_id="ci.deception.main",
        apparatus_id="test",
        channel="ci.deception.triggers",
        payload={
            "canary_id": canary_id,
            "requestor_ip": ip,
            "method": "GET",
            "path": path,
            "headers": {},
            "triggered_at": "2024-01-01T00:00:00",
        },
        priority=Priority.CRITICAL,
    )


# ── Core attribution logic ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_single_hit_no_profile():
    """One trigger below threshold → no packet emitted."""
    spine = Spine()
    node = AttributionNode(_make_spec(min_hits=2), spine)
    await node.setup()

    sub = spine.subscribe("ci.attribution.events")
    await spine.publish("ci.deception.triggers", _trigger_packet("1.2.3.4"))
    await node.run()

    # Queue should be empty
    assert sub._queue.empty()
    sub.close()
    await node.stop()


@pytest.mark.asyncio
async def test_two_hits_same_ip_emits_profile():
    """Two triggers from same IP → AttributionPacket emitted."""
    spine = Spine()
    node = AttributionNode(_make_spec(min_hits=2), spine)
    await node.setup()

    sub = spine.subscribe("ci.attribution.events")

    for _ in range(2):
        await spine.publish("ci.deception.triggers", _trigger_packet("10.0.0.1"))
        await node.run()

    result = await asyncio.wait_for(sub.get(), timeout=2.0)
    assert result.packet_type == "AttributionPacket"
    assert result.payload["adversary_ip"] == "10.0.0.1"
    assert result.payload["session_hit_count"] == 2
    sub.close()
    await node.stop()


@pytest.mark.asyncio
async def test_different_ips_isolated():
    """Two different IPs produce separate profiles and don't cross-contaminate."""
    spine = Spine()
    node = AttributionNode(_make_spec(min_hits=2), spine)
    await node.setup()

    sub = spine.subscribe("ci.attribution.events")

    # 2 hits from IP A
    for _ in range(2):
        await spine.publish("ci.deception.triggers", _trigger_packet("192.168.1.1"))
        await node.run()

    # 2 hits from IP B
    for _ in range(2):
        await spine.publish("ci.deception.triggers", _trigger_packet("192.168.1.2"))
        await node.run()

    packets = []
    while not sub._queue.empty():
        packets.append(sub._queue.get_nowait())

    ips = {p.payload["adversary_ip"] for p in packets}
    assert "192.168.1.1" in ips
    assert "192.168.1.2" in ips
    sub.close()
    await node.stop()


@pytest.mark.asyncio
async def test_session_window_expires():
    """Hits outside session_window are pruned and don't count toward threshold."""
    spine = Spine()
    node = AttributionNode(_make_spec(min_hits=2, session_window=1), spine)
    await node.setup()

    sub = spine.subscribe("ci.attribution.events")

    # First hit
    await spine.publish("ci.deception.triggers", _trigger_packet("5.5.5.5"))
    await node.run()

    # Manually age out the first hit
    node._sessions["5.5.5.5"][0]["ts"] = time.time() - 2.0

    # Second hit — window expired, so effective count is still 1
    await spine.publish("ci.deception.triggers", _trigger_packet("5.5.5.5"))
    await node.run()

    assert sub._queue.empty()
    sub.close()
    await node.stop()


@pytest.mark.asyncio
async def test_severity_escalates_with_hits():
    """Severity escalates: 2-4 hits MEDIUM, 5-9 HIGH, 10+ CRITICAL."""
    spine = Spine()
    node = AttributionNode(_make_spec(min_hits=2), spine)
    await node.setup()

    sub = spine.subscribe("ci.attribution.events")

    # 2 hits → MEDIUM
    for _ in range(2):
        await spine.publish("ci.deception.triggers", _trigger_packet("77.7.7.7"))
        await node.run()

    first = await asyncio.wait_for(sub.get(), timeout=2.0)
    assert first.payload["severity"] == "MEDIUM"

    # 3 more hits → still checking (5 total → HIGH)
    for _ in range(3):
        await spine.publish("ci.deception.triggers", _trigger_packet("77.7.7.7"))
        await node.run()

    last = None
    while not sub._queue.empty():
        last = sub._queue.get_nowait()
    assert last is not None
    assert last.payload["severity"] == "HIGH"
    sub.close()
    await node.stop()


@pytest.mark.asyncio
async def test_attribution_payload_fields():
    """AttributionPacket has all required fields."""
    spine = Spine()
    node = AttributionNode(_make_spec(min_hits=2), spine)
    await node.setup()

    sub = spine.subscribe("ci.attribution.events")

    for _ in range(2):
        await spine.publish("ci.deception.triggers",
                            _trigger_packet("3.3.3.3", canary_id="canary-99", path="/admin/secret"))
        await node.run()

    result = await asyncio.wait_for(sub.get(), timeout=2.0)
    p = result.payload
    assert "adversary_ip" in p
    assert "session_hit_count" in p
    assert "canaries_hit" in p
    assert "paths_probed" in p
    assert "first_seen" in p
    assert "last_seen" in p
    assert "assessment" in p
    assert "severity" in p
    assert "canary-99" in p["canaries_hit"]
    assert "/admin/secret" in p["paths_probed"]
    sub.close()
    await node.stop()
