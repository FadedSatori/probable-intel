"""Redis Streams backend for the Spine.

Drop-in replacement for the in-process ``Spine`` (same publish / subscribe /
unsubscribe / channel_metrics / all_channel_names interface), so harvesters,
analysts and sinks can run in separate processes or hosts that share a Redis.

Each channel is the stream ``pi:<channel>``. Each subscription gets its own
consumer group, so — exactly like the in-process Spine — every subscriber sees
every packet published after it subscribed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator
from uuid import uuid4

from .channel import ChannelMetrics
from .packet import IntelPacket

log = logging.getLogger(__name__)

STREAM_PREFIX = "pi:"
_FIELD = "packet"


class RedisSpine:
    def __init__(
        self,
        url: str,
        *,
        max_len: int = 10_000,
        block_ms: int = 1_000,
        idle_sleep: float = 0.05,
        client: Any = None,
        control_client: Any = None,
    ) -> None:
        import redis
        import redis.asyncio

        self._client = client or redis.asyncio.from_url(url)
        # subscribe()/close() are synchronous in the Spine interface, and the
        # consumer group must exist before subscribe() returns (or packets
        # published in between are missed), so group setup/teardown uses a
        # small synchronous connection.
        self._control = control_client or redis.Redis.from_url(url)
        self._max_len = max_len
        self._block_ms = block_ms
        self._idle_sleep = idle_sleep
        self._metrics: dict[str, ChannelMetrics] = {}
        self._readers: set[asyncio.Task] = set()

    @staticmethod
    def stream_key(channel_name: str) -> str:
        return STREAM_PREFIX + channel_name

    def _metrics_for(self, channel_name: str) -> ChannelMetrics:
        return self._metrics.setdefault(channel_name, ChannelMetrics())

    async def publish(self, channel_name: str, packet: IntelPacket) -> None:
        if packet.is_expired():
            log.debug("dropping expired packet %s on %s", packet.packet_id, channel_name)
            return
        await self._client.xadd(
            self.stream_key(channel_name),
            {_FIELD: json.dumps(packet.to_dict(), default=str)},
            maxlen=self._max_len,
            approximate=True,
        )
        metrics = self._metrics_for(channel_name)
        metrics.published += 1
        metrics.last_publish_ts = time.time()

    def subscribe(self, channel_name: str) -> "RedisSubscription":
        group = f"sub-{uuid4().hex}"
        self._control.xgroup_create(self.stream_key(channel_name), group, id="$", mkstream=True)
        self._metrics_for(channel_name)
        return RedisSubscription(self, channel_name, group)

    def unsubscribe(self, channel_name: str, group: str) -> None:
        try:
            self._control.xgroup_destroy(self.stream_key(channel_name), group)
        except Exception as e:  # connection already gone at shutdown
            log.debug("redis spine: could not destroy group %s on %s: %s", group, channel_name, e)

    def channel_metrics(self, channel_name: str) -> ChannelMetrics | None:
        return self._metrics.get(channel_name)

    def all_channel_names(self) -> list[str]:
        return list(self._metrics)

    async def aclose(self) -> None:
        for reader in self._readers:
            reader.cancel()
        await asyncio.gather(*self._readers, return_exceptions=True)
        await self._client.aclose()
        self._control.close()


class RedisSubscription:
    """One channel, read through a private consumer group.

    A background reader task blocks on Redis and fills a local queue; get()
    only awaits that queue. BaseNode._wait_any cancels losing get() calls
    constantly, so keeping Redis commands out of get() makes it exactly as
    cancel-safe as the in-process Spine (redis-py can swallow a cancellation
    that lands mid-command).
    """

    _CONSUMER = "c"

    def __init__(self, spine: RedisSpine, channel_name: str, group: str) -> None:
        self._spine = spine
        self._channel_name = channel_name
        self._group = group
        self._key = spine.stream_key(channel_name)
        self._queue: asyncio.Queue[IntelPacket] = asyncio.Queue(maxsize=5_000)
        self._reader: asyncio.Task | None = None

    async def __aiter__(self) -> AsyncIterator[IntelPacket]:
        while True:
            yield await self.get()

    async def get(self) -> IntelPacket:
        if self._reader is None:
            self._reader = asyncio.create_task(
                self._read_forever(), name=f"redis-spine:{self._channel_name}"
            )
            self._spine._readers.add(self._reader)
            self._reader.add_done_callback(self._spine._readers.discard)
        return await self._queue.get()

    async def _read_forever(self) -> None:
        client = self._spine._client
        while True:
            # Honour a cancellation redis-py swallowed mid-command (see class doc).
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise asyncio.CancelledError
            try:
                # NOACK: the group is private to this subscription and dies with
                # it, so acknowledgements would buy nothing.
                resp = await client.xreadgroup(
                    self._group, self._CONSUMER, {self._key: ">"},
                    count=100, block=self._spine._block_ms, noack=True,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("redis spine: read failed on %s: %s", self._channel_name, e)
                await asyncio.sleep(1.0)
                continue
            entries = resp[0][1] if resp else []
            if not entries:
                # Real Redis only returns empty after block_ms; this guards servers
                # that ignore BLOCK (e.g. fakeredis) against a busy loop.
                await asyncio.sleep(self._spine._idle_sleep)
                continue
            for _entry_id, fields in entries:
                raw = fields.get(_FIELD.encode()) or fields.get(_FIELD)
                await self._queue.put(IntelPacket.from_dict(json.loads(raw)))

    def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
        self._spine.unsubscribe(self._channel_name, self._group)
