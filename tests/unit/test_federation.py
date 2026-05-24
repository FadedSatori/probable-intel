"""Unit tests for federation layer."""
from __future__ import annotations

import asyncio
import pytest

from probable_intel.nexus.spec import FederationSpec, FederationPeerSpec
from probable_intel.spine.spine import Spine
from probable_intel.spine.packet import IntelPacket, Priority, TrustLevel
from probable_intel.hub.federation import FederatedSpine, _downgrade_trust, _packet_to_dict, _dict_to_packet


def _make_packet(priority=Priority.NORMAL, trust=TrustLevel.RESTRICTED, tags=None) -> IntelPacket:
    return IntelPacket(
        packet_type="TestPacket",
        source_node_id="test.node",
        apparatus_id="test",
        channel="threat.test",
        payload={"data": "intel"},
        priority=priority,
        trust_level=trust,
        tags=tags or [],
    )


def _make_fed_spec(peers=None, auto_critical=True, ingest_channels=None) -> FederationSpec:
    spec = FederationSpec(
        enabled=True,
        auto_federate_critical=auto_critical,
        ingest_channels=ingest_channels or [],
    )
    if peers:
        spec.peers = peers
    return spec


# ── Trust downgrade ───────────────────────────────────────────────────────────

def test_trust_downgrade_restricted_to_unclassified():
    """RESTRICTED packet crosses boundary and becomes UNCLASSIFIED."""
    result = _downgrade_trust(TrustLevel.RESTRICTED)
    assert result == TrustLevel.UNCLASSIFIED


def test_trust_downgrade_classified_to_restricted():
    result = _downgrade_trust(TrustLevel.CLASSIFIED)
    assert result == TrustLevel.RESTRICTED


def test_trust_downgrade_unclassified_stays_unclassified():
    """UNCLASSIFIED cannot be downgraded further."""
    result = _downgrade_trust(TrustLevel.UNCLASSIFIED)
    assert result == TrustLevel.UNCLASSIFIED


# ── Enqueue filtering ─────────────────────────────────────────────────────────

def test_no_federate_tag_blocked():
    """Packets tagged 'no-federate' are never enqueued."""
    spine = Spine()
    fed = FederatedSpine(spine, _make_fed_spec())
    packet = _make_packet(tags=["no-federate"], priority=Priority.CRITICAL)
    fed.enqueue(packet)
    assert fed._outbound.empty()


def test_critical_auto_federated():
    """CRITICAL packet is enqueued when auto_federate_critical=True."""
    spine = Spine()
    fed = FederatedSpine(spine, _make_fed_spec(auto_critical=True))
    packet = _make_packet(priority=Priority.CRITICAL)
    fed.enqueue(packet)
    assert not fed._outbound.empty()


def test_critical_not_federated_when_disabled():
    """CRITICAL packet is NOT enqueued when auto_federate_critical=False."""
    spine = Spine()
    fed = FederatedSpine(spine, _make_fed_spec(auto_critical=False))
    packet = _make_packet(priority=Priority.CRITICAL)
    fed.enqueue(packet)
    assert fed._outbound.empty()


def test_explicit_federate_tag_enqueues_any_priority():
    """'federate' tag causes packet to be pushed regardless of priority."""
    spine = Spine()
    fed = FederatedSpine(spine, _make_fed_spec(auto_critical=False))
    packet = _make_packet(priority=Priority.LOW, tags=["federate"])
    fed.enqueue(packet)
    assert not fed._outbound.empty()


# ── Ingest + trust downgrade ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ingest_downgrades_trust_and_publishes():
    """ingest() downgrades trust and republishes on local Spine."""
    spine = Spine()
    fed = FederatedSpine(spine, _make_fed_spec(ingest_channels=["threat.*"]))

    sub = spine.subscribe("threat.test")

    packet = _make_packet(trust=TrustLevel.RESTRICTED)
    data = _packet_to_dict(packet)
    await fed.ingest(data, peer_url="peer-a")

    result = await asyncio.wait_for(sub.get(), timeout=2.0)
    assert result.trust_level == TrustLevel.UNCLASSIFIED  # downgraded
    assert "federated" in result.tags
    sub.close()


@pytest.mark.asyncio
async def test_ingest_respects_channel_filter():
    """Packets not matching ingest_channels are dropped."""
    spine = Spine()
    fed = FederatedSpine(spine, _make_fed_spec(ingest_channels=["raw.*"]))  # only raw.*

    sub = spine.subscribe("threat.test")

    packet = _make_packet(trust=TrustLevel.RESTRICTED)  # channel="threat.test"
    data = _packet_to_dict(packet)
    await fed.ingest(data, peer_url="peer-b")

    # Should not appear on spine
    assert sub._queue.empty()
    sub.close()


# ── SSE stream subscribers ────────────────────────────────────────────────────

def test_sse_subscriber_receives_enqueued_packets():
    """SSE subscriber queue gets a copy of each enqueued packet."""
    spine = Spine()
    fed = FederatedSpine(spine, _make_fed_spec(auto_critical=True))

    q = fed.add_stream_subscriber()
    packet = _make_packet(priority=Priority.CRITICAL)
    fed.enqueue(packet)

    assert not q.empty()
    received = q.get_nowait()
    assert received.packet_id == packet.packet_id


def test_sse_subscriber_removed():
    """remove_stream_subscriber cleans up the queue."""
    spine = Spine()
    fed = FederatedSpine(spine, _make_fed_spec())
    q = fed.add_stream_subscriber()
    assert q in fed._stream_subs
    fed.remove_stream_subscriber(q)
    assert q not in fed._stream_subs


# ── Serialization round-trip ──────────────────────────────────────────────────

def test_packet_serialization_round_trip():
    """_packet_to_dict → _dict_to_packet preserves key fields."""
    packet = _make_packet(priority=Priority.HIGH, trust=TrustLevel.CLASSIFIED)
    data = _packet_to_dict(packet)
    restored = _dict_to_packet(data)

    assert str(restored.packet_id) == str(packet.packet_id)
    assert restored.packet_type == packet.packet_type
    assert restored.priority == packet.priority
    assert restored.payload == packet.payload
