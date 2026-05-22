from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from ..base import BaseNode
from ...spine.packet import IntelPacket, Priority, TrustLevel

if TYPE_CHECKING:
    from ...nexus.spec import NodeSpec
    from ...spine.spine import Spine

log = logging.getLogger(__name__)


class SentimentNode(BaseNode):
    """VADER-primary sentiment analysis; emits SentimentPackets."""

    def __init__(self, spec: "NodeSpec", spine: "Spine") -> None:
        super().__init__(spec, spine)
        self._analyzer = None
        self._subscriptions = []
        self._emit_channel: str = ""
        self._emit_priority: Priority = Priority.NORMAL
        self._llm_threshold: float = 0.4

    async def setup(self) -> None:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        self._analyzer = SentimentIntensityAnalyzer()

        if self.spec.emit:
            self._emit_channel = self.spec.emit.channel
            self._emit_priority = Priority[self.spec.emit.priority.upper()]

        backend = self.spec.backend
        self._llm_threshold = float(backend.get("llm_threshold", 0.4))

        self._subscriptions = [
            self.spine.subscribe(ch) for ch in self.spec.subscribe_channels
        ]

    async def teardown(self) -> None:
        for sub in self._subscriptions:
            sub.close()

    async def run(self) -> None:
        if not self._subscriptions:
            await asyncio.sleep(1)
            return

        tasks = [asyncio.create_task(sub.get()) for sub in self._subscriptions]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()

        packet: IntelPacket = next(iter(done)).result()
        await self._process(packet)

    async def _process(self, packet: IntelPacket) -> None:
        text = packet.payload.get("content") or packet.payload.get("body", "")
        if not text or not self._analyzer:
            return

        scores = self._analyzer.polarity_scores(text[:10_000])
        compound = scores["compound"]
        confidence = abs(compound)

        out = packet.relay(
            self.node_id,
            self._emit_channel,
            packet_type="SentimentPacket",
            payload={
                **packet.payload,
                "sentiment_score": compound,
                "sentiment_pos": scores["pos"],
                "sentiment_neg": scores["neg"],
                "sentiment_neu": scores["neu"],
                "sentiment_confidence": confidence,
                "sentiment_backend": "vader",
            },
            confidence=confidence,
        )
        await self.emit(self._emit_channel, out)
        log.debug(
            "node %s sentiment=%.3f (compound) for packet %s",
            self.node_id,
            compound,
            packet.packet_id,
        )
