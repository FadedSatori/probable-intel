from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import AsyncIterator, Callable, Awaitable

from .channel import Channel, ChannelMetrics
from .packet import IntelPacket

log = logging.getLogger(__name__)


class Spine:
    """In-process async message bus. Swap for RedisSpine to go distributed."""

    def __init__(self) -> None:
        self._metrics: dict[str, ChannelMetrics] = defaultdict(ChannelMetrics)
        self._subscribers: dict[str, list[Channel]] = defaultdict(list)

    async def publish(self, channel_name: str, packet: IntelPacket) -> None:
        if packet.is_expired():
            log.debug("dropping expired packet %s on %s", packet.packet_id, channel_name)
            return
        # Packets live only in subscriber inboxes; the Spine itself keeps counts, not copies.
        metrics = self._metrics[channel_name]
        metrics.published += 1
        metrics.last_publish_ts = time.time()
        for inbox in self._subscribers.get(channel_name, []):
            if not await inbox.put(packet):
                metrics.dropped += 1

    def subscribe(self, *channel_names: str) -> "SpineSubscription":
        """Return one inbox fed by every named channel, highest priority first.

        A consumer of several channels waits in a single place. Racing one get() per
        channel loses packets: more than one get can finish before the consumer looks.
        """
        inbox = Channel(", ".join(channel_names), max_size=5_000)
        for name in channel_names:
            self._subscribers[name].append(inbox)
        return SpineSubscription(channel_names, inbox, self)

    def unsubscribe(self, channel_name: str, q: Channel) -> None:
        subs = self._subscribers.get(channel_name, [])
        if q in subs:
            subs.remove(q)

    def channel_metrics(self, channel_name: str) -> ChannelMetrics | None:
        """Publish and drop counts for a channel; None until something is published on it."""
        return self._metrics.get(channel_name)

    def all_channel_names(self) -> list[str]:
        return list(self._metrics)


class SpineSubscription:
    def __init__(
        self,
        channel_names: tuple[str, ...],
        queue: Channel,
        spine: Spine,
    ) -> None:
        self._channel_names = channel_names
        self._queue = queue
        self._spine = spine

    async def __aiter__(self) -> AsyncIterator[IntelPacket]:
        while True:
            packet = await self._queue.get()
            yield packet

    async def get(self) -> IntelPacket:
        return await self._queue.get()

    def close(self) -> None:
        for name in self._channel_names:
            self._spine.unsubscribe(name, self._queue)
