"""Unit tests for OpSecNode, DeceptionNode and FingerprintDefenseNode."""
from __future__ import annotations

import asyncio

import pytest

from probable_intel.nexus.spec import EmitSpec, HoneypotSpec, NodeSpec
from probable_intel.nodes.counterintel.deception_node import DeceptionNode
from probable_intel.nodes.counterintel.fingerprint_node import FingerprintDefenseNode
from probable_intel.nodes.counterintel.opsec_node import OpSecNode
from probable_intel.spine.packet import IntelPacket, Priority, TrustLevel
from probable_intel.spine.spine import Spine


def _spec(node_type: str, node_id: str, **kwargs) -> NodeSpec:
    return NodeSpec(node_type=node_type, node_id=node_id, apparatus_id="test", **kwargs)


def _audit(**payload) -> IntelPacket:
    return IntelPacket(
        packet_type="AuditRequestEvent",
        source_node_id="ci.fingerprint.test",
        apparatus_id="test",
        channel="ci.opsec.audit",
        payload=payload,
    )


async def _drain(sub, timeout: float = 0.1) -> list:
    packets = []
    while True:
        try:
            packets.append(await asyncio.wait_for(sub.get(), timeout))
        except asyncio.TimeoutError:
            return packets


# ── OpSecNode ────────────────────────────────────────────────────────────────

async def test_opsec_flags_identity_reuse_over_the_limit():
    spine = Spine()
    node = OpSecNode(_spec("OpSecNode", "ci.opsec.test", config={"max_uses_per_window": 3}), spine)
    await node.setup()
    events = spine.subscribe("ci.opsec.events")

    for _ in range(3):
        await node._process_audit(_audit(event_type="request", identity_profile_id="profile-a"))
    assert await _drain(events) == []  # at the limit, not over it

    await node._process_audit(_audit(event_type="request", identity_profile_id="profile-a"))
    [event] = await _drain(events)
    assert event.payload["event_type"] == "identity_reuse_detected"
    assert event.payload["profile_id"] == "profile-a"
    assert event.payload["use_count"] == 4
    assert event.payload["severity"] == "HIGH"
    assert event.trust_level == TrustLevel.CLASSIFIED
    await node.teardown()


async def test_opsec_detects_reuse_when_limit_exceeds_old_fixed_history_cap():
    spine = Spine()
    node = OpSecNode(
        _spec("OpSecNode", "ci.opsec.test", config={"max_uses_per_window": 250}), spine
    )
    await node.setup()
    events = spine.subscribe("ci.opsec.events")

    for _ in range(251):
        await node._process_audit(_audit(event_type="request", identity_profile_id="profile-b"))

    assert [e.payload["use_count"] for e in await _drain(events)] == [251]
    await node.teardown()


async def test_opsec_identities_are_tracked_independently():
    spine = Spine()
    node = OpSecNode(_spec("OpSecNode", "ci.opsec.test", config={"max_uses_per_window": 2}), spine)
    await node.setup()
    events = spine.subscribe("ci.opsec.events")

    for profile in ("a", "b", "a", "b"):
        await node._process_audit(_audit(event_type="request", identity_profile_id=profile))

    assert await _drain(events) == []
    await node.teardown()


async def test_opsec_proxy_pool_critical_event_is_emitted_before_returning():
    spine = Spine()
    node = OpSecNode(_spec("OpSecNode", "ci.opsec.test"), spine)
    await node.setup()
    events = spine.subscribe("ci.opsec.events")

    for i in range(5):  # 0 of 5 healthy < 20%
        await node._process_audit(_audit(event_type="proxy_health", proxy_id=f"p{i}", health_score=0.1))

    critical = [e for e in await _drain(events, timeout=0.05)]
    assert critical and all(e.payload["event_type"] == "proxy_pool_critical" for e in critical)
    assert critical[-1].payload["total"] == 5
    await node.teardown()


# ── DeceptionNode ────────────────────────────────────────────────────────────

def _deception(spine: Spine) -> DeceptionNode:
    return DeceptionNode(_spec(
        "DeceptionNode", "ci.deception.test",
        honeypots=[HoneypotSpec(type="fake_api_endpoint", path="/api/x", canary_id="canary-1")],
        emit=EmitSpec(channel="ci.deception.triggers", priority="critical"),
    ), spine)


async def test_deception_trigger_emits_critical_packet_and_counts_hits():
    spine = Spine()
    node = _deception(spine)
    await node.setup()
    triggers = spine.subscribe("ci.deception.triggers")

    await node.trigger("canary-1", requestor_ip="10.0.0.9", path="/api/x", headers={"UA": "curl"})
    await node.run()

    [packet] = await _drain(triggers)
    assert packet.packet_type == "DeceptionTriggerPacket"
    assert packet.priority == Priority.CRITICAL
    assert packet.payload["severity"] == "CRITICAL"  # so AlertNode forwards it
    assert packet.payload["canary_id"] == "canary-1"
    assert packet.payload["requestor_ip"] == "10.0.0.9"
    assert packet.payload["headers"] == {"UA": "curl"}
    assert node._canary_registry["canary-1"]["trigger_count"] == 1
    await node.teardown()


async def test_deception_unknown_canary_still_fires():
    spine = Spine()
    node = _deception(spine)
    await node.setup()
    triggers = spine.subscribe("ci.deception.triggers")

    await node.trigger("never-registered", requestor_ip="10.0.0.9")
    await node.run()

    [packet] = await _drain(triggers)
    assert packet.payload["canary_id"] == "never-registered"
    await node.teardown()


async def test_deception_canary_tokens_are_unique_beacon_paths():
    node = _deception(Spine())
    await node.setup()
    first, second = node.issue_canary_token("doc-1"), node.issue_canary_token("doc-1")
    assert first.startswith("/beacon/") and second.startswith("/beacon/")
    assert first != second
    await node.teardown()


# ── FingerprintDefenseNode ───────────────────────────────────────────────────

async def test_fingerprint_report_request_returns_bounded_jitter_and_audits():
    spine = Spine()
    node = FingerprintDefenseNode(_spec("FingerprintDefenseNode", "ci.fingerprint.test"), spine)
    await node.setup()
    audit = spine.subscribe("ci.opsec.audit")

    delays = [await node.report_request("profile-a", "https://x.test", "feed.a") for _ in range(50)]
    for _ in range(50):
        await node.run()

    assert all(0.5 <= d <= 30.0 for d in delays)
    events = await _drain(audit)
    assert len(events) == 50
    assert events[0].payload["event_type"] == "request"  # what OpSecNode consumes
    assert events[0].payload["identity_profile_id"] == "profile-a"
    await node.teardown()


@pytest.mark.parametrize("config", [{"jitter_mu": 0}, {"jitter_mu": -1}, {"jitter_sigma": -0.1}])
async def test_fingerprint_rejects_invalid_jitter_config(config):
    node = FingerprintDefenseNode(
        _spec("FingerprintDefenseNode", "ci.fingerprint.test", config=config), Spine()
    )
    with pytest.raises(ValueError, match="jitter"):
        await node.setup()
