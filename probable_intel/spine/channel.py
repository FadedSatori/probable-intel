from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field

from .packet import IntelPacket, Priority


@dataclass
class ChannelMetrics:
    """Counters for one inbox.

    The Spine also keeps one per channel name, filling only the publish side
    (published, dropped, last_publish_ts): reading happens in the inboxes.
    """

    published: int = 0
    consumed: int = 0
    dropped: int = 0
    max_depth_seen: int = 0
    last_publish_ts: float = 0.0


class Channel:
    """Priority-aware asyncio channel on the Spine."""

    def __init__(self, name: str, max_size: int = 10_000) -> None:
        self.name = name
        self.metrics = ChannelMetrics()
        self._max_size = max_size  # per lane
        # One lane per priority, highest first. _has_packets is set exactly while a lane holds one.
        self._lanes: dict[Priority, deque[IntelPacket]] = {p: deque() for p in reversed(Priority)}
        self._has_packets = asyncio.Event()

    async def put(self, packet: IntelPacket) -> bool:
        """Queue the packet. Return False if its lane is full and it was dropped."""
        lane = self._lanes[packet.priority]
        if len(lane) >= self._max_size:
            self.metrics.dropped += 1
            return False
        lane.append(packet)
        self._has_packets.set()
        self.metrics.published += 1
        self.metrics.last_publish_ts = time.time()
        self.metrics.max_depth_seen = max(self.metrics.max_depth_seen, self.depth())
        return True

    async def get(self) -> IntelPacket:
        """Drain highest-priority lane first; falls through to lower lanes."""
        while self.empty():
            await self._has_packets.wait()
        return self.get_nowait()

    def get_nowait(self) -> IntelPacket:
        """Like get(), but raise asyncio.QueueEmpty instead of waiting."""
        lane = next((lane for lane in self._lanes.values() if lane), None)
        if lane is None:
            raise asyncio.QueueEmpty
        packet = lane.popleft()
        if not self.depth():
            self._has_packets.clear()
        self.metrics.consumed += 1
        return packet

    def empty(self) -> bool:
        return not self._has_packets.is_set()

    def depth(self) -> int:
        return sum(len(lane) for lane in self._lanes.values())

    qsize = depth  # asyncio.Queue's name for it

    def __repr__(self) -> str:
        return f"Channel({self.name!r}, depth={self.depth()})"
