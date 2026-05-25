from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import TYPE_CHECKING

from ..base import BaseNode
from ...spine.packet import IntelPacket, Priority

if TYPE_CHECKING:
    from ...nexus.spec import NodeSpec
    from ...spine.spine import Spine

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a threat intelligence analyst. Be concise and factual. "
    "Never speculate beyond what the data shows."
)

_USER_TEMPLATE = (
    "Summarize the following {n} intelligence items in 2-3 sentences, "
    "identifying the key narrative thread and most significant threat actor or vulnerability.\n\n"
    "Context:\n{context}"
)


class NarrativeNode(BaseNode):
    """LLM-powered narrative synthesis across a rolling window of packets.

    Accumulates packets from subscribed channels; when the window fills or
    emit_interval elapses, asks the LLM to synthesize a narrative summary
    and emits a NarrativePacket.

    Requires spec.llm to be set; logs a warning and no-ops otherwise.
    """

    def __init__(self, spec: "NodeSpec", spine: "Spine") -> None:
        super().__init__(spec, spine)
        self._subscriptions: list = []
        self._emit_channel: str = ""
        self._emit_priority: Priority = Priority.NORMAL
        self._llm_router = None
        self._window: deque = deque()
        self._window_size: int = 10
        self._emit_interval: float = 300.0
        self._last_emit: float = 0.0

    async def setup(self) -> None:
        if self.spec.llm is None:
            log.warning(
                "node %s: NarrativeNode requires an 'llm:' config block; "
                "node will produce no output without it",
                self.node_id,
            )
        else:
            try:
                from ...llm.router import LLMRouter
                self._llm_router = LLMRouter.from_spec(self.spec.llm)
            except Exception as e:
                log.warning("node %s: LLM setup failed: %s", self.node_id, e)

        cfg = self.spec.config
        self._window_size = int(cfg.get("narrative_window", 10))
        self._emit_interval = float(cfg.get("emit_interval_seconds", 300))
        self._window = deque(maxlen=self._window_size)

        if self.spec.emit:
            self._emit_channel = self.spec.emit.channel
            self._emit_priority = Priority[self.spec.emit.priority.upper()]

        self._subscriptions = [
            self.spine.subscribe(ch) for ch in self.spec.subscribe_channels
        ]
        self._last_emit = time.time()

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
        text = packet.payload.get("content") or packet.payload.get("body") or packet.payload.get("summary", "")
        if text:
            self._window.append({
                "source": packet.source_node_id,
                "channel": packet.channel,
                "text": text[:500],
                "timestamp": packet.timestamp_utc.isoformat(),
            })

        now = time.time()
        window_full = len(self._window) >= self._window_size
        interval_elapsed = (now - self._last_emit) >= self._emit_interval

        if (window_full or interval_elapsed) and len(self._window) > 0:
            await self._emit_narrative(packet)
            self._last_emit = now
            self._window.clear()

    async def _emit_narrative(self, trigger: IntelPacket) -> None:
        if not self._emit_channel or self._llm_router is None:
            return

        items = list(self._window)
        context = "\n\n".join(
            f"[{i['timestamp']} | {i['channel']}]\n{i['text']}"
            for i in items
        )
        prompt = _USER_TEMPLATE.format(n=len(items), context=context)

        try:
            summary = await self._llm_router.complete(prompt, system=_SYSTEM_PROMPT, max_tokens=256)
        except Exception as e:
            log.warning("node %s: narrative LLM call failed: %s", self.node_id, e)
            return

        window_start = items[0]["timestamp"] if items else ""
        window_end = items[-1]["timestamp"] if items else ""

        out = trigger.relay(
            self.node_id,
            self._emit_channel,
            packet_type="NarrativePacket",
            payload={
                "summary": summary,
                "source_count": len(items),
                "window_start": window_start,
                "window_end": window_end,
            },
            priority=self._emit_priority,
        )
        await self.emit(self._emit_channel, out)
        log.info("node %s: narrative emitted (%d sources)", self.node_id, len(items))
