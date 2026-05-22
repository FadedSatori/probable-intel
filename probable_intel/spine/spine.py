from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import AsyncIterator, Callable, Awaitable

from .channel import Channel, ChannelMetrics
from .packet import IntelPacket

log = logging.getLogger(__name__)


class Spine:
    """In-process async message bus. Swap for RedisSpine to go distributed."""

    def __init__(self) -> None:
        self._channels: dict[str, Channel] = {}
        self._subscribers: dict[str, list[asyncio.Queue[IntelPacket]]] = defaultdict(list)

    def _ensure_channel(self, name: str) -> Channel:
        if name not in self._channels:
            self._channels[name] = Channel(name)
        return self._channels[name]

    async def publish(self, channel_name: str, packet: IntelPacket) -> None:
        if packet.is_expired():
            log.debug("dropping expired packet %s on %s", packet.packet_id, channel_name)
            return
        ch = self._ensure_channel(channel_name)
        await ch.put(packet)
        for sub_queue in self._subscribers.get(channel_name, []):
            if not sub_queue.full():
                await sub_queue.put(packet)

    def subscribe(self, channel_name: str) -> "SpineSubscription":
        q: asyncio.Queue[IntelPacket] = asyncio.Queue(maxsize=5_000)
        self._subscribers[channel_name].append(q)
        return SpineSubscription(channel_name, q, self)

    def unsubscribe(self, channel_name: str, q: asyncio.Queue[IntelPacket]) -> None:
        subs = self._subscribers.get(channel_name, [])
        if q in subs:
            subs.remove(q)

    def channel_metrics(self, channel_name: str) -> ChannelMetrics | None:
        ch = self._channels.get(channel_name)
        return ch.metrics if ch else None

    def all_channel_names(self) -> list[str]:
        return list(self._channels.keys())


class SpineSubscription:
    def __init__(
        self,
        channel_name: str,
        queue: asyncio.Queue[IntelPacket],
        spine: Spine,
    ) -> None:
        self._channel_name = channel_name
        self._queue = queue
        self._spine = spine

    async def __aiter__(self) -> AsyncIterator[IntelPacket]:
        while True:
            packet = await self._queue.get()
            yield packet

    async def get(self) -> IntelPacket:
        return await self._queue.get()

    def close(self) -> None:
        self._spine.unsubscribe(self._channel_name, self._queue)
