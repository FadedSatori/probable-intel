import asyncio
import pytest
from probable_intel.spine.packet import IntelPacket, Priority, TrustLevel
from probable_intel.spine.spine import Spine


def make_packet(channel: str = "test.channel", priority: Priority = Priority.NORMAL) -> IntelPacket:
    return IntelPacket(
        packet_type="TestPacket",
        source_node_id="test-node",
        apparatus_id="test-apparatus",
        channel=channel,
        payload={"data": "hello"},
        priority=priority,
    )


@pytest.mark.asyncio
async def test_publish_and_subscribe():
    spine = Spine()
    sub = spine.subscribe("test.channel")
    packet = make_packet()
    await spine.publish("test.channel", packet)
    received = await asyncio.wait_for(sub.get(), timeout=1.0)
    assert received.packet_id == packet.packet_id


@pytest.mark.asyncio
async def test_expired_packet_dropped():
    spine = Spine()
    sub = spine.subscribe("test.channel")
    packet = make_packet()
    packet.ttl_seconds = 0
    await asyncio.sleep(0.01)
    await spine.publish("test.channel", packet)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(sub.get(), timeout=0.1)


@pytest.mark.asyncio
async def test_priority_ordering():
    spine = Spine()
    from probable_intel.spine.channel import Channel
    channel = Channel("prio.test")
    low = make_packet(priority=Priority.LOW)
    high = make_packet(priority=Priority.HIGH)
    await channel.put(low)
    await channel.put(high)
    first = await channel.get()
    assert first.priority == Priority.HIGH


def test_packet_dedup_hash():
    p1 = IntelPacket(
        packet_type="T", source_node_id="n", apparatus_id="a",
        channel="c", payload={"x": 1}
    )
    p2 = IntelPacket(
        packet_type="T", source_node_id="n", apparatus_id="a",
        channel="c", payload={"x": 1}
    )
    assert p1.source_hash == p2.source_hash


def test_packet_relay_provenance():
    p = make_packet()
    relayed = p.relay("relay-node", "other.channel")
    assert relayed.provenance[0] == "relay-node"
    assert "test-node" in relayed.provenance
    assert relayed.channel == "other.channel"


def test_trust_levels():
    assert TrustLevel.TOP_SECRET > TrustLevel.CLASSIFIED
    assert TrustLevel.CLASSIFIED > TrustLevel.RESTRICTED
    assert TrustLevel.RESTRICTED > TrustLevel.UNCLASSIFIED
