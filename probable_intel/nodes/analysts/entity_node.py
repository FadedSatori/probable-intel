from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..base import BaseNode
from ...spine.packet import IntelPacket, TrustLevel

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
        self._confidence_min: float = 0.0
        self._llm_router = None

    async def setup(self) -> None:
        try:
            import spacy
            self._nlp = spacy.load(_SPACY_MODEL)
        except ImportError:
            log.warning("spaCy not installed; EntityExtractorNode will pass packets through unchanged")
            self._nlp = None
        except OSError:
            log.warning(
                "spaCy model %r not found; run: python -m spacy download %s  "
                "-- node will pass packets through unchanged until model is available",
                _SPACY_MODEL, _SPACY_MODEL,
            )
            self._nlp = None

        if self.spec.llm is not None and self._nlp is None:
            try:
                from ...llm.router import LLMRouter
                self._llm_router = LLMRouter.from_spec(self.spec.llm)
                log.info("node %s: LLM entity extraction enabled (spaCy unavailable)", self.node_id)
            except Exception as e:
                log.warning("node %s: LLM setup failed: %s", self.node_id, e)

        self._subscriptions = [
            self.spine.subscribe(ch) for ch in self.spec.subscribe_channels
        ]
        self._confidence_min = self.spec.config.get("confidence_min", 0.0)

    async def teardown(self) -> None:
        for sub in self._subscriptions:
            sub.close()

    async def run(self) -> None:
        packet = await self._wait_any(self._subscriptions)
        if packet is None:
            return
        await self._process(packet)

    async def _process(self, packet: IntelPacket) -> None:
        import json
        text = packet.payload.get("content") or packet.payload.get("body", "")
        if not text:
            return

        # Graceful degradation: try LLM fallback if spaCy unavailable
        if not self._nlp:
            entities = []
            tags = list(packet.tags)
            if self._llm_router is not None:
                try:
                    raw = await self._llm_router.complete(
                        'Extract named entities as JSON array: '
                        '[{"text":"...","type":"ORG|CVE|GPE|PERSON|PRODUCT"},...]\n\n'
                        f'{text[:3000]}',
                        max_tokens=512,
                    )
                    # Extract JSON array from response (may have surrounding text)
                    match = __import__("re").search(r"\[.*\]", raw, __import__("re").DOTALL)
                    if match:
                        entities = json.loads(match.group())
                        tags.append("llm-entities")
                except Exception as e:
                    log.debug("node %s: LLM entity fallback failed: %s", self.node_id, e)

            if self._emit_channel:
                out = packet.relay(
                    self.node_id, self._emit_channel,
                    packet_type="EntityPacket",
                    payload={**packet.payload, "entities": entities, "entity_count": len(entities)},
                )
                out.tags = tags
                await self.emit(self._emit_channel, out)
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
