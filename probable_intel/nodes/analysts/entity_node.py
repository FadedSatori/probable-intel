from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import BaseNode
from ...spine.packet import IntelPacket, Priority, TrustLevel

if TYPE_CHECKING:
    from ...nexus.spec import NodeSpec
    from ...spine.spine import Spine

log = logging.getLogger(__name__)

_SPACY_MODEL = "en_core_web_sm"


class EntityExtractorNode(BaseNode):
    """NER via spaCy; emits EntityPackets."""

    def __init__(self, spec: "NodeSpec", spine: "Spine") -> None:
        super().__init__(spec, spine)
        self._nlp = None
        self._subscriptions = []
        self._emit_channel: str = ""
        self._emit_priority: Priority = Priority.NORMAL
        self._confidence_min: float = 0.0

    async def setup(self) -> None:
        import spacy

        model = _SPACY_MODEL
        try:
            self._nlp = spacy.load(model)
        except OSError:
            log.warning(
                "spaCy model %r not found; run: python -m spacy download %s",
                model,
                model,
            )
            self._nlp = None

        if self.spec.emit:
            self._emit_channel = self.spec.emit.channel
            self._emit_priority = Priority[self.spec.emit.priority.upper()]

        self._subscriptions = [
            self.spine.subscribe(ch) for ch in self.spec.subscribe_channels
        ]
        self._confidence_min = self.spec.config.get("confidence_min", 0.0)

    async def teardown(self) -> None:
        for sub in self._subscriptions:
            sub.close()

    async def run(self) -> None:
        if not self._subscriptions:
            import asyncio
            await asyncio.sleep(1)
            return

        import asyncio

        tasks = [asyncio.create_task(sub.get()) for sub in self._subscriptions]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()

        packet: IntelPacket = next(iter(done)).result()
        await self._process(packet)

    async def _process(self, packet: IntelPacket) -> None:
        text = packet.payload.get("content") or packet.payload.get("body", "")
        if not text or not self._nlp:
            return

        doc = self._nlp(text[:100_000])
        entities = []
        for ent in doc.ents:
            entities.append({
                "text": ent.text,
                "type": ent.label_,
                "start": ent.start_char,
                "end": ent.end_char,
            })

        if not entities:
            return

        out = packet.relay(
            self.node_id,
            self._emit_channel,
            packet_type="EntityPacket",
            payload={
                **packet.payload,
                "entities": entities,
                "entity_count": len(entities),
            },
        )
        await self.emit(self._emit_channel, out)
        log.debug("node %s extracted %d entities from packet %s", self.node_id, len(entities), packet.packet_id)
