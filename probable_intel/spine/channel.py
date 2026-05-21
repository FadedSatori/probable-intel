from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from .packet import IntelPacket, Priority


@dataclass
class ChannelMetrics:
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
        self._queues: dict[Priority, asyncio.Queue[IntelPacket]] = {
            p: asyncio.Queue(maxsize=max_size) for p in Priority
        }

    async def put(self, packet: IntelPacket) -> None:
        q = self._queues[packet.priority]
        if q.full():
            self.metrics.dropped += 1
            return
        await q.put(packet)
        self.metrics.published += 1
        self.metrics.last_publish_ts = time.time()
        depth = sum(q.qsize() for q in self._queues.values())
        if depth > self.metrics.max_depth_seen:
            self.metrics.max_depth_seen = depth

    async def get(self) -> IntelPacket:
        """Drain highest-priority lane first; falls through to lower lanes."""
        for priority in reversed(Priority):
            q = self._queues[priority]
            if not q.empty():
                packet = await q.get()
                self.metrics.consumed += 1
                return packet
        # all empty — wait on any queue via asyncio.wait
        tasks = {p: asyncio.ensure_future(q.get()) for p, q in self._queues.items()}
        done, pending = await asyncio.wait(
            tasks.values(), return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
        packet = next(iter(done)).result()
        self.metrics.consumed += 1
        return packet

    def depth(self) -> int:
        return sum(q.qsize() for q in self._queues.values())

    def __repr__(self) -> str:
        return f"Channel({self.name!r}, depth={self.depth()})"
