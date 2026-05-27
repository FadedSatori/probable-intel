from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING

from ..base import BaseNode
from ..analysts.threat_node import _SEVERITY_RANK
from ...spine.packet import IntelPacket, Priority

if TYPE_CHECKING:
    from ...nexus.spec import NodeSpec
    from ...spine.spine import Spine

log = logging.getLogger(__name__)

_RULE_BASED_DEFAULTS = {
    # threat signal → add a keyword filter on the entity channel
    "ThreatPacket": ("add_keyword_filter", "analyst.entities.*", {}),
    # anomaly → expand collection on source channel
    "AnomalyPacket": ("expand_collection", "feed.*", {}),
    # attribution event → deprioritize suspected adversary traffic
    "AttributionPacket": ("deprioritize", "*", {}),
}


class TaskRouterNode(BaseNode):
    """Autonomous collection coordinator — closes the OODA loop.

    Watches high-severity threat/anomaly/attribution signals and emits
    TaskDirectivePackets that the Hub applies at runtime to redirect
    collection resources toward emerging threats.

    LLM-enhanced when spec.llm is set; falls back to rule-based directives.
    """

    def __init__(self, spec: "NodeSpec", spine: "Spine") -> None:
        super().__init__(spec, spine)
        self._subscriptions: list = []
        self._emit_channel: str = ""
        self._emit_priority: Priority = Priority.HIGH
        self._min_severity: int = _SEVERITY_RANK["HIGH"]
        self._cooldown: float = 300.0
        self._max_per_hour: int = 10
        self._llm_router = None

        # topic → last directive timestamp (for cooldown)
        self._last_directive: dict[str, float] = {}
        # sliding window of directive timestamps (for rate-limit)
        self._directive_times: list[float] = []

    async def setup(self) -> None:
        cfg = self.spec.config
        min_sev = str(cfg.get("min_severity", "HIGH")).upper()
        self._min_severity = _SEVERITY_RANK.get(min_sev, _SEVERITY_RANK["HIGH"])
        self._cooldown = float(cfg.get("directive_cooldown_seconds", 300))
        self._max_per_hour = int(cfg.get("max_directives_per_hour", 10))

        if self.spec.llm is not None:
            try:
                from ...llm.router import LLMRouter
                self._llm_router = LLMRouter.from_spec(self.spec.llm)
            except Exception as e:
                log.warning("node %s: LLM setup failed: %s", self.node_id, e)

        if self.spec.emit:
            self._emit_channel = self.spec.emit.channel
            self._emit_priority = Priority[self.spec.emit.priority.upper()]

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
        await self._analyze_signal(packet)

    async def _analyze_signal(self, packet: IntelPacket) -> None:
        payload = packet.payload
        severity = str(payload.get("severity") or payload.get("max_severity", "LOW")).upper()
        if _SEVERITY_RANK.get(severity, 0) < self._min_severity:
            return

        # Rate limit: max N directives per hour
        now = time.time()
        self._directive_times = [t for t in self._directive_times if now - t < 3600]
        if len(self._directive_times) >= self._max_per_hour:
            log.debug("node %s: rate limit reached (%d/hr)", self.node_id, self._max_per_hour)
            return

        # Cooldown: deduplicate by topic
        topic = self._extract_topic(packet)
        last = self._last_directive.get(topic, 0.0)
        if now - last < self._cooldown:
            log.debug("node %s: cooldown active for topic %r", self.node_id, topic)
            return

        directive = await self._build_directive(packet, severity, topic)
        if directive is None:
            return

        self._last_directive[topic] = now
        self._directive_times.append(now)

        out = packet.relay(
            self.node_id,
            self._emit_channel,
            packet_type="TaskDirectivePacket",
            payload={**directive, "source_signal": str(packet.packet_id), "severity": severity},
            priority=self._emit_priority,
        )
        await self.emit(self._emit_channel, out)
        log.info(
            "node %s: directive %r → %r (severity=%s)",
            self.node_id, directive.get("directive_type"), directive.get("target_node_id"), severity,
        )

    def _extract_topic(self, packet: IntelPacket) -> str:
        """Derive a stable dedup key from a packet."""
        payload = packet.payload
        # Use matched rule label if present
        matched = payload.get("matched_rules") or payload.get("rules_matched", [])
        if matched and isinstance(matched, list) and matched[0]:
            label = matched[0].get("label", "") if isinstance(matched[0], dict) else str(matched[0])
            if label:
                return label
        # Fall back to channel
        return packet.channel

    async def _build_directive(
        self, packet: IntelPacket, severity: str, topic: str
    ) -> dict | None:
        payload = packet.payload

        if self._llm_router is not None:
            summary = self._summarize_packet(payload)
            prompt = (
                f"You are a threat intel coordinator. A {severity} signal arrived: {summary}\n"
                "What collection action should be taken? Reply with JSON only:\n"
                '{"directive_type": "expand_collection"|"add_keyword_filter"|"pause_channel"|"deprioritize", '
                '"target_node_id": "<node-id-or-wildcard>", '
                '"parameters": {}, '
                '"rationale": "<one sentence>", '
                '"ttl_seconds": 3600}'
            )
            try:
                raw = await self._llm_router.complete(prompt, max_tokens=200)
                # Use raw_decode to extract the first valid JSON object without
                # greedy regex (which matches first { to last }, breaking on trailing text)
                start = raw.find("{")
                if start >= 0:
                    directive, _ = json.JSONDecoder().raw_decode(raw[start:])
                    if isinstance(directive, dict):
                        directive.setdefault("ttl_seconds", 3600)
                        return directive
            except Exception as e:
                log.debug("node %s: LLM directive failed: %s", self.node_id, e)

        # Rule-based fallback
        ptype = packet.packet_type
        dtype, target, params = _RULE_BASED_DEFAULTS.get(
            ptype, ("add_keyword_filter", "analyst.entities.*", {})
        )
        # Try to extract a useful keyword/entity from the payload
        entities = payload.get("entities", [])
        first = entities[0] if entities else None
        keyword = first.get("text", topic) if isinstance(first, dict) else topic

        return {
            "directive_type": dtype,
            "target_node_id": target,
            "parameters": {**params, "keyword": keyword},
            "rationale": f"rule-based: {severity} {ptype} on {packet.channel}",
            "ttl_seconds": 3600,
        }

    def _summarize_packet(self, payload: dict) -> str:
        parts = []
        if "channel" in payload:
            parts.append(f"channel={payload['channel']}")
        if "matched_rules" in payload:
            rules = payload["matched_rules"]
            labels = [r.get("label", "") for r in rules if isinstance(r, dict)]
            parts.append(f"rules={labels}")
        if "metric" in payload:
            parts.append(f"metric={payload['metric']} value={payload.get('current_value')}")
        if "entities" in payload:
            ents = [e.get("text", "") for e in payload["entities"][:3] if isinstance(e, dict)]
            parts.append(f"entities={ents}")
        return "; ".join(parts) or str(payload)[:200]
