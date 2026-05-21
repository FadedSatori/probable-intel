from __future__ import annotations

import asyncio
import collections
import logging
import time
from typing import TYPE_CHECKING

from ..base import BaseNode
from ...spine.packet import IntelPacket, Priority, TrustLevel

if TYPE_CHECKING:
    from ...nexus.spec import NodeSpec
    from ...spine.spine import Spine

log = logging.getLogger(__name__)

_AUDIT_CHANNEL = "ci.opsec.audit"
_EVENTS_CHANNEL = "ci.opsec.events"


class OpSecNode(BaseNode):
    """Monitors own node operational security; detects identity reuse and request patterns."""

    HEARTBEAT_INTERVAL = 30.0

    def __init__(self, spec: "NodeSpec", spine: "Spine") -> None:
        super().__init__(spec, spine)
        self._sub = None
        self._identity_window: int = 3600
        self._max_uses_per_window: int = 50
        self._identity_usage: dict[str, collections.deque] = collections.defaultdict(
            lambda: collections.deque(maxlen=200)
        )
        self._proxy_health: dict[str, float] = {}

    async def setup(self) -> None:
        self._sub = self.spine.subscribe(_AUDIT_CHANNEL)
        config = self.spec.config
        self._identity_window = config.get("identity_window_seconds", 3600)
        self._max_uses_per_window = config.get("max_uses_per_window", 50)
        log.info("node %s: monitoring opsec audit channel", self.node_id)

    async def teardown(self) -> None:
        if self._sub:
            self._sub.close()

    async def run(self) -> None:
        if not self._sub:
            await asyncio.sleep(1)
            return
        try:
            packet = await asyncio.wait_for(self._sub.get(), timeout=5.0)
        except asyncio.TimeoutError:
            return
        await self._process_audit(packet)

    async def _process_audit(self, packet: IntelPacket) -> None:
        event_type = packet.payload.get("event_type", "")
        if event_type == "request":
            await self._check_identity_reuse(packet)
        elif event_type == "proxy_health":
            self._update_proxy_health(packet)

    async def _check_identity_reuse(self, packet: IntelPacket) -> None:
        profile_id = packet.payload.get("identity_profile_id", "unknown")
        now = time.time()
        dq = self._identity_usage[profile_id]
        dq.append(now)

        cutoff = now - self._identity_window
        recent = [ts for ts in dq if ts >= cutoff]

        if len(recent) > self._max_uses_per_window:
            log.warning(
                "opsec: identity %r used %d times in %ds window (limit %d)",
                profile_id,
                len(recent),
                self._identity_window,
                self._max_uses_per_window,
            )
            await self._emit_opsec_event(
                "identity_reuse_detected",
                {
                    "profile_id": profile_id,
                    "use_count": len(recent),
                    "window_seconds": self._identity_window,
                    "recommendation": "rotate_identity",
                },
                Priority.HIGH,
            )

    def _update_proxy_health(self, packet: IntelPacket) -> None:
        proxy_id = packet.payload.get("proxy_id", "")
        health_score = float(packet.payload.get("health_score", 1.0))
        self._proxy_health[proxy_id] = health_score

        healthy = sum(1 for s in self._proxy_health.values() if s >= 0.5)
        total = len(self._proxy_health)
        if total > 0 and healthy / total < 0.2:
            log.warning("opsec: proxy pool critically low (%d/%d healthy)", healthy, total)
            asyncio.create_task(
                self._emit_opsec_event(
                    "proxy_pool_critical",
                    {"healthy": healthy, "total": total},
                    Priority.HIGH,
                )
            )

    async def _emit_opsec_event(self, event_type: str, data: dict, priority: Priority) -> None:
        packet = IntelPacket(
            packet_type="OpSecEvent",
            source_node_id=self.node_id,
            apparatus_id=self.apparatus_id,
            channel=_EVENTS_CHANNEL,
            payload={"event_type": event_type, **data},
            priority=priority,
            trust_level=TrustLevel.CLASSIFIED,
        )
        await self.emit(_EVENTS_CHANNEL, packet)
