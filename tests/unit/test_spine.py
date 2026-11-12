import asyncio
import gc
import weakref

import pytest
from probable_intel.spine.channel import Channel
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
async def test_one_subscription_covers_several_channels_in_publish_order():
    spine = Spine()
    sub = spine.subscribe("ch.a", "ch.b")
    first, second = make_packet("ch.b"), make_packet("ch.a")
    await spine.publish("ch.b", first)
    await spine.publish("ch.a", second)
    received = [await asyncio.wait_for(sub.get(), timeout=1.0) for _ in range(2)]
    assert [p.packet_id for p in received] == [first.packet_id, second.packet_id]


@pytest.mark.asyncio
async def test_closing_a_subscription_stops_delivery_from_all_its_channels():
    spine = Spine()
    sub = spine.subscribe("ch.a", "ch.b")
    sub.close()
    await spine.publish("ch.a", make_packet("ch.a"))
    await spine.publish("ch.b", make_packet("ch.b"))
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(sub.get(), timeout=0.05)


@pytest.mark.asyncio
async def test_subscriber_receives_higher_priority_packets_first():
    spine = Spine()
    sub = spine.subscribe("test.channel")
    low, critical, normal = (
        make_packet(priority=p) for p in (Priority.LOW, Priority.CRITICAL, Priority.NORMAL)
    )
    for packet in (low, critical, normal):
        await spine.publish("test.channel", packet)
    received = [await asyncio.wait_for(sub.get(), timeout=1.0) for _ in range(3)]
    assert [p.packet_id for p in received] == [critical.packet_id, normal.packet_id, low.packet_id]


@pytest.mark.asyncio
async def test_spine_lets_go_of_a_packet_once_its_subscriber_has_taken_it():
    spine = Spine()
    sub = spine.subscribe("test.channel")
    packet = make_packet()
    still_alive = weakref.ref(packet)
    await spine.publish("test.channel", packet)
    received = await sub.get()
    assert received is packet

    del packet, received
    gc.collect()
    assert still_alive() is None, "the Spine kept a packet nobody can read"


@pytest.mark.asyncio
async def test_channel_metrics_stay_true_on_a_long_lived_channel():
    spine = Spine()
    sub = spine.subscribe("test.channel")
    for _ in range(10_001):
        await spine.publish("test.channel", make_packet())
        await sub.get()

    metrics = spine.channel_metrics("test.channel")
    assert (metrics.published, metrics.dropped) == (10_001, 0)


@pytest.mark.asyncio
async def test_channel_metrics_count_a_delivery_refused_by_a_full_inbox():
    spine = Spine()
    spine.subscribe("test.channel")  # never read: its NORMAL lane holds 5,000
    for _ in range(5_001):
        await spine.publish("test.channel", make_packet())

    metrics = spine.channel_metrics("test.channel")
    assert (metrics.published, metrics.dropped) == (5_001, 1)


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


@pytest.mark.asyncio
async def test_channel_keeps_every_packet_when_two_lanes_fill_while_it_waits():
    channel = Channel("prio.test")
    waiting = asyncio.create_task(channel.get())
    await asyncio.sleep(0)  # the getter is now waiting on an empty channel
    low, critical = make_packet(priority=Priority.LOW), make_packet(priority=Priority.CRITICAL)
    await channel.put(low)
    await channel.put(critical)

    first = await asyncio.wait_for(waiting, timeout=1.0)
    try:
        second = await asyncio.wait_for(channel.get(), timeout=1.0)
    except TimeoutError:
        pytest.fail("the packet on the other lane was lost")
    assert [first.packet_id, second.packet_id] == [critical.packet_id, low.packet_id]


@pytest.mark.asyncio
async def test_channel_can_be_drained_without_waiting():
    channel = Channel("prio.test")
    low, high = make_packet(priority=Priority.LOW), make_packet(priority=Priority.HIGH)
    await channel.put(low)
    await channel.put(high)

    assert channel.qsize() == 2
    assert [channel.get_nowait().packet_id, channel.get_nowait().packet_id] == [high.packet_id, low.packet_id]
    assert channel.empty()
    with pytest.raises(asyncio.QueueEmpty):
        channel.get_nowait()


@pytest.mark.asyncio
async def test_full_lane_drops_only_its_own_priority():
    channel = Channel("prio.test", max_size=2)
    for _ in range(3):
        await channel.put(make_packet(priority=Priority.LOW))
    critical = make_packet(priority=Priority.CRITICAL)
    await channel.put(critical)

    assert channel.metrics.dropped == 1  # the third LOW packet
    assert (await channel.get()).packet_id == critical.packet_id
    assert channel.depth() == 2


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
