"""Unit tests for RedisSpine.

Every test runs against fakeredis; set REDIS_URL (e.g. redis://127.0.0.1:6379/0)
to also run them against a real Redis server.
"""
from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import fakeredis
import pytest

from probable_intel.nexus.spec import NodeSpec
from probable_intel.nodes.base import BaseNode
from probable_intel.spine.packet import IntelPacket, Priority, TrustLevel
from probable_intel.spine.redis_spine import RedisSpine

BACKENDS = ["fakeredis"] + (["redis"] if os.environ.get("REDIS_URL") else [])


@pytest.fixture(params=BACKENDS)
async def spine(request):
    if request.param == "fakeredis":
        server = fakeredis.FakeServer()
        s = RedisSpine(
            "redis://fake",
            client=fakeredis.FakeAsyncRedis(server=server),
            control_client=fakeredis.FakeRedis(server=server),
            block_ms=100,
            idle_sleep=0.01,
        )
    else:
        s = RedisSpine(os.environ["REDIS_URL"], block_ms=100)
    yield s
    await s.aclose()


def _channel() -> str:
    return f"test.{uuid4().hex[:8]}"


def _packet(channel: str, **payload) -> IntelPacket:
    return IntelPacket(
        packet_type="TestPacket",
        source_node_id="test.source",
        apparatus_id="test",
        channel=channel,
        payload=payload or {"data": "hello"},
        priority=Priority.HIGH,
        trust_level=TrustLevel.RESTRICTED,
        tags=["t1"],
    )


async def _get(sub, timeout: float = 2.0) -> IntelPacket:
    return await asyncio.wait_for(sub.get(), timeout)


async def test_round_trip_preserves_every_field(spine):
    ch = _channel()
    sub = spine.subscribe(ch)
    sent = _packet(ch, title="APT28", score=-0.8, nested={"a": [1, 2]})

    await spine.publish(ch, sent)

    assert (await _get(sub)).to_dict() == sent.to_dict()
    sub.close()


async def test_every_subscriber_receives_every_packet(spine):
    ch = _channel()
    first, second = spine.subscribe(ch), spine.subscribe(ch)
    sent = _packet(ch)

    await spine.publish(ch, sent)

    assert (await _get(first)).packet_id == sent.packet_id
    assert (await _get(second)).packet_id == sent.packet_id
    first.close()
    second.close()


async def test_only_packets_published_after_subscribe_are_delivered(spine):
    ch = _channel()
    await spine.publish(ch, _packet(ch, n=1))
    sub = spine.subscribe(ch)
    await spine.publish(ch, _packet(ch, n=2))

    assert (await _get(sub)).payload == {"n": 2}
    with pytest.raises(asyncio.TimeoutError):
        await _get(sub, timeout=0.3)
    sub.close()


async def test_close_destroys_the_consumer_group(spine):
    ch = _channel()
    sub = spine.subscribe(ch)
    key = spine.stream_key(ch)
    assert len(spine._control.xinfo_groups(key)) == 1

    sub.close()

    assert spine._control.xinfo_groups(key) == []


async def test_expired_packet_is_not_published(spine):
    ch = _channel()
    sub = spine.subscribe(ch)
    stale = _packet(ch)
    stale.ttl_seconds = 0
    await asyncio.sleep(0.01)

    await spine.publish(ch, stale)

    assert spine.channel_metrics(ch).published == 0
    with pytest.raises(asyncio.TimeoutError):
        await _get(sub, timeout=0.3)
    sub.close()


async def test_metrics_and_channel_names(spine):
    ch = _channel()
    sub = spine.subscribe(ch)
    for _ in range(3):
        await spine.publish(ch, _packet(ch))

    assert spine.channel_metrics(ch).published == 3
    assert ch in spine.all_channel_names()
    assert spine.channel_metrics("never.seen") is None
    sub.close()


async def test_cancelled_get_loses_nothing(spine):
    """BaseNode._wait_any cancels losing get() calls constantly; no packet may vanish."""
    ch = _channel()
    sub = spine.subscribe(ch)
    for _ in range(5):
        waiting = asyncio.create_task(sub.get())
        await asyncio.sleep(0.01)
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
    for n in (1, 2):
        await spine.publish(ch, _packet(ch, n=n))

    assert [(await _get(sub)).payload["n"] for _ in range(2)] == [1, 2]
    sub.close()


async def test_close_stops_the_background_reader(spine):
    ch = _channel()
    sub = spine.subscribe(ch)
    await spine.publish(ch, _packet(ch))
    await _get(sub)  # starts the reader
    reader = sub._reader

    sub.close()
    await asyncio.wait([reader], timeout=2.0)

    assert reader.done()
    assert reader not in spine._readers


class _Node(BaseNode):
    async def run(self) -> None:
        await asyncio.sleep(0)


async def test_wait_any_across_redis_subscriptions_loses_nothing(spine):
    """BaseNode._wait_any cancels the losing reads every call; nothing may go missing."""
    channels = [_channel(), _channel()]
    subs = [spine.subscribe(ch) for ch in channels]
    node = _Node(NodeSpec(node_type="Test", node_id="test.node", apparatus_id="test"), spine)
    for i in range(15):
        for ch in channels:
            await spine.publish(ch, _packet(ch, i=i))

    received = set()
    for _ in range(30):
        packet = await asyncio.wait_for(node._wait_any(subs), timeout=2.0)
        received.add((packet.channel, packet.payload["i"]))

    assert received == {(ch, i) for ch in channels for i in range(15)}
    for sub in subs:
        sub.close()


async def test_reader_honours_a_cancellation_the_client_swallowed():
    """redis-py can swallow a cancel mid-command; the reader must still stop."""
    server = fakeredis.FakeServer()
    client = fakeredis.FakeAsyncRedis(server=server)
    real_xreadgroup = client.xreadgroup

    swallowed = []

    async def swallowing_xreadgroup(*args, **kwargs):
        try:
            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            if swallowed:
                raise
            swallowed.append(True)  # swallow the first cancel, as the real client can
            return []
        return await real_xreadgroup(*args, **kwargs)

    client.xreadgroup = swallowing_xreadgroup
    spine = RedisSpine("redis://fake", client=client, control_client=fakeredis.FakeRedis(server=server),
                       block_ms=100, idle_sleep=0.01)
    sub = spine.subscribe(_channel())
    waiting = asyncio.create_task(sub.get())  # starts the reader
    await asyncio.sleep(0.02)
    reader = sub._reader

    sub.close()  # cancels the reader mid-command; the client swallows it
    done, _ = await asyncio.wait([reader], timeout=1.0)

    assert swallowed
    assert reader in done and reader.cancelled()
    waiting.cancel()
    await spine.aclose()
