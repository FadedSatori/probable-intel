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


@pytest.mark.asyncio
async def test_subscriber_queue_full_logs_warning(caplog):
    """Dropping packets when a subscriber queue is full emits a log warning."""
    import logging
    spine = Spine()
    # Subscribe and fill the queue to capacity
    sub = spine.subscribe("overflow.channel")
    # Use a tiny queue by patching maxsize — easier to just fill it
    # We can't easily patch maxsize=5000, so instead we verify the log fires
    # by filling the internal queue directly
    q = sub._queue
    for _ in range(q.maxsize):
        await q.put(make_packet("overflow.channel"))
    # Now publish one more — queue is full, should log a warning
    with caplog.at_level(logging.WARNING, logger="probable_intel.spine.spine"):
        await spine.publish("overflow.channel", make_packet("overflow.channel"))
    assert any("subscriber queue full" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_channel_get_cancelled_cancels_inner_tasks():
    """Channel.get() must not leak tasks when cancelled from outside."""
    from probable_intel.spine.channel import Channel
    ch = Channel("cancel.test")

    async def get_with_cancel():
        task = asyncio.create_task(ch.get())
        await asyncio.sleep(0)  # let task start
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Should complete without hanging
    await asyncio.wait_for(get_with_cancel(), timeout=1.0)
