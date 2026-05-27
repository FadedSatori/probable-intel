from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import TYPE_CHECKING, Any

from ..base import BaseNode
from ..analysts.threat_node import _SEVERITY_RANK
from ...spine.packet import IntelPacket

if TYPE_CHECKING:
    from ...nexus.spec import NodeSpec
    from ...spine.spine import Spine

log = logging.getLogger(__name__)


def _hit_severity(hit_count: int) -> str:
    if hit_count >= 10:
        return "CRITICAL"
    if hit_count >= 5:
        return "HIGH"
    return "MEDIUM"


class AttributionNode(BaseNode):
    """Passive adversary profiling from honeypot trigger events.

    Subscribes to ci.deception.triggers. Groups hits from the same IP into
    sessions, builds an AdversaryProfile per attacker, and emits
    AttributionPackets. Optionally injects profiles into a KnowledgeGraphNode.

    Strictly passive — zero outbound requests or DNS lookups.
    """

    def __init__(self, spec: "NodeSpec", spine: "Spine") -> None:
        super().__init__(spec, spine)
        self._subscriptions: list = []
        self._session_window: float = 3600.0
        self._min_hits: int = 2
        self._llm_router = None

        # ip → list of trigger dicts (within window)
        self._sessions: dict[str, list[dict]] = defaultdict(list)
        # ip → cumulative profile dict
        self._adversaries: dict[str, dict] = {}
        # Reference injected by Hub after construction
        self._kg_node: Any = None

    async def setup(self) -> None:
        cfg = self.spec.config
        self._session_window = float(cfg.get("session_window_seconds", 3600))
        self._min_hits = int(cfg.get("min_hits_to_profile", 2))

        if self.spec.llm is not None:
            try:
                from ...llm.router import LLMRouter
                self._llm_router = LLMRouter.from_spec(self.spec.llm)
            except Exception as e:
                log.warning("node %s: LLM setup failed: %s", self.node_id, e)

        self._subscriptions = [
            self.spine.subscribe(ch) for ch in self.spec.subscribe_channels
        ]

    async def teardown(self) -> None:
        for sub in self._subscriptions:
            sub.close()

    async def run(self) -> None:
        packet = await self._wait_any(self._subscriptions)
        if packet is None:
            return
        await self._process_trigger(packet)

    async def _process_trigger(self, packet: IntelPacket) -> None:
        payload = packet.payload
        ip = str(payload.get("requestor_ip", "unknown"))
        canary_id = str(payload.get("canary_id", ""))
        path = str(payload.get("path", ""))
        triggered_at = str(payload.get("triggered_at", packet.timestamp_utc.isoformat()))
        now = time.time()

        # Add hit and prune stale entries
        self._sessions[ip].append({
            "canary_id": canary_id,
            "path": path,
            "triggered_at": triggered_at,
            "ts": now,
        })
        self._sessions[ip] = [
            h for h in self._sessions[ip]
            if now - h["ts"] <= self._session_window
        ]

        hit_count = len(self._sessions[ip])
        if hit_count < self._min_hits:
            return

        # Build/update adversary profile
        profile = self._adversaries.setdefault(ip, {
            "first_seen": triggered_at,
            "hit_count": 0,
            "canaries_hit": [],
            "paths_probed": [],
        })
        profile["hit_count"] = hit_count
        profile["last_seen"] = triggered_at
        canaries = list({h["canary_id"] for h in self._sessions[ip]})
        paths = list({h["path"] for h in self._sessions[ip]})
        profile["canaries_hit"] = canaries
        profile["paths_probed"] = paths

        severity = _hit_severity(hit_count)

        # Write into KnowledgeGraph if available
        if self._kg_node is not None:
            try:
                kg = self._kg_node
                key = f"ADVERSARY:{ip}"
                if kg._graph is not None:
                    if kg._graph.has_node(key):
                        kg._graph.nodes[key]["count"] += 1
                        kg._graph.nodes[key]["last_seen"] = triggered_at
                        kg._graph.nodes[key]["hit_count"] = hit_count
                    else:
                        kg._graph.add_node(
                            key,
                            text=ip,
                            entity_type="ADVERSARY",
                            count=1,
                            hit_count=hit_count,
                            first_seen=profile["first_seen"],
                            last_seen=triggered_at,
                        )
                    for cid in canaries:
                        canary_key = f"CANARY:{cid}"
                        if not kg._graph.has_node(canary_key):
                            kg._graph.add_node(canary_key, text=cid, entity_type="CANARY", count=1,
                                               first_seen=triggered_at, last_seen=triggered_at)
                        if not kg._graph.has_edge(key, canary_key):
                            kg._graph.add_edge(key, canary_key, weight=1)
                        else:
                            kg._graph[key][canary_key]["weight"] += 1
            except Exception as e:
                log.debug("node %s: KG update failed: %s", self.node_id, e)

        # LLM assessment
        assessment = "automated-probe"
        if self._llm_router is not None:
            try:
                prompt = (
                    f"This IP hit {hit_count} honeypot canaries: {canaries}. "
                    f"Paths probed: {paths}. "
                    "In one sentence, characterize this activity: automated scanner, targeted probe, or crawler."
                )
                assessment = await self._llm_router.complete(prompt, max_tokens=60)
                assessment = assessment.strip()
            except Exception as e:
                log.debug("node %s: LLM assessment failed: %s", self.node_id, e)

        if not self._emit_channel:
            return

        out = packet.relay(
            self.node_id,
            self._emit_channel,
            packet_type="AttributionPacket",
            payload={
                "adversary_ip": ip,
                "session_hit_count": hit_count,
                "canaries_hit": canaries,
                "paths_probed": paths,
                "first_seen": profile["first_seen"],
                "last_seen": triggered_at,
                "assessment": assessment,
                "severity": severity,
            },
            priority=self._emit_priority,
        )
        await self.emit(self._emit_channel, out)
        log.warning(
            "node %s: %s adversary %s — %d hits, canaries=%s",
            self.node_id, severity, ip, hit_count, canaries,
        )
