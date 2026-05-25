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
    """VADER-primary sentiment analysis; emits SentimentPackets.

    When spec.llm is set and VADER confidence is below llm_threshold,
    falls back to an LLM for a more nuanced sentiment score.
    """

    def __init__(self, spec: "NodeSpec", spine: "Spine") -> None:
        super().__init__(spec, spine)
        self._analyzer = None
        self._subscriptions = []
        self._emit_channel: str = ""
        self._emit_priority: Priority = Priority.NORMAL
        self._llm_threshold: float = 0.4
        self._llm_router = None

    async def setup(self) -> None:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        self._analyzer = SentimentIntensityAnalyzer()

        if self.spec.emit:
            self._emit_channel = self.spec.emit.channel
            self._emit_priority = Priority[self.spec.emit.priority.upper()]

        backend = self.spec.backend
        self._llm_threshold = float(backend.get("llm_threshold", 0.4))

        if self.spec.llm is not None:
            try:
                from ...llm.router import LLMRouter
                self._llm_router = LLMRouter.from_spec(self.spec.llm)
                log.info("node %s: LLM fallback enabled (threshold=%.2f)", self.node_id, self._llm_threshold)
            except Exception as e:
                log.warning("node %s: LLM setup failed: %s", self.node_id, e)

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
        backend_used = "vader"
        tags = list(packet.tags)

        # LLM fallback when VADER is uncertain
        if self._llm_router is not None and confidence < self._llm_threshold:
            try:
                raw = await self._llm_router.complete(
                    f"Score sentiment -1.0 to 1.0. Reply with only a float.\n\n{text[:2000]}",
                    max_tokens=8,
                )
                llm_score = float(raw.strip())
                if -1.0 <= llm_score <= 1.0:
                    compound = llm_score
                    confidence = abs(llm_score)
                    backend_used = "llm"
                    tags.append("llm-sentiment")
            except Exception as e:
                log.debug("node %s: LLM sentiment fallback failed: %s", self.node_id, e)

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
                "sentiment_backend": backend_used,
            },
            confidence=confidence,
        )
        out.tags = tags
        await self.emit(self._emit_channel, out)
        log.debug(
            "node %s sentiment=%.3f (%s) for packet %s",
            self.node_id,
            compound,
            backend_used,
            packet.packet_id,
        )
