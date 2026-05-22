from __future__ import annotations

import asyncio
import logging
import secrets
import time
from typing import TYPE_CHECKING

from ..base import BaseNode
from ...spine.packet import IntelPacket, Priority, TrustLevel

if TYPE_CHECKING:
    from ...nexus.spec import NodeSpec
    from ...spine.spine import Spine

log = logging.getLogger(__name__)

_TRIGGER_CHANNEL = "ci.deception.triggers"


class DeceptionNode(BaseNode):
    """Manages honeypots and canary tokens; fires DeceptionTriggerPackets on contact."""

    HEARTBEAT_INTERVAL = 60.0

    def __init__(self, spec: "NodeSpec", spine: "Spine") -> None:
        super().__init__(spec, spine)
        self._canary_registry: dict[str, dict] = {}
        self._honeypot_specs: list[dict] = []
        self._trigger_queue: asyncio.Queue = asyncio.Queue()
        self._fastapi_app = None

    async def setup(self) -> None:
        for hp in self.spec.honeypots:
            canary_id = hp.canary_id
            self._canary_registry[canary_id] = {
                "type": hp.type,
                "path": hp.path,
                "canary_id": canary_id,
                "registered_at": time.time(),
                "trigger_count": 0,
            }
            self._honeypot_specs.append({
                "type": hp.type,
                "path": hp.path,
                "canary_id": canary_id,
            })
            log.info("deception: registered honeypot %r at %s", canary_id, hp.path)

    async def run(self) -> None:
        try:
            trigger = await asyncio.wait_for(self._trigger_queue.get(), timeout=5.0)
        except asyncio.TimeoutError:
            return
        await self._emit_trigger(trigger)

    async def trigger(
        self,
        canary_id: str,
        requestor_ip: str = "0.0.0.0",
        method: str = "GET",
        path: str = "",
        headers: dict | None = None,
    ) -> None:
        """Called by HubAPI honeypot routes when a canary fires."""
        entry = self._canary_registry.get(canary_id)
        if entry:
            entry["trigger_count"] += 1
        await self._trigger_queue.put({
            "canary_id": canary_id,
            "requestor_ip": requestor_ip,
            "method": method,
            "path": path,
            "headers": headers or {},
            "triggered_at": time.time(),
        })

    def issue_canary_token(self, document_id: str) -> str:
        """Generate a unique canary token URL to embed in an exported document."""
        token = secrets.token_urlsafe(16)
        self._canary_registry[f"doc:{document_id}:{token}"] = {
            "type": "document_canary",
            "document_id": document_id,
            "token": token,
            "registered_at": time.time(),
            "trigger_count": 0,
        }
        return f"/beacon/{token}"

    async def _emit_trigger(self, trigger: dict) -> None:
        packet = IntelPacket(
            packet_type="DeceptionTriggerPacket",
            source_node_id=self.node_id,
            apparatus_id=self.apparatus_id,
            channel=_TRIGGER_CHANNEL,
            payload=trigger,
            priority=Priority.CRITICAL,
            trust_level=TrustLevel.CLASSIFIED,
        )
        await self.emit(_TRIGGER_CHANNEL, packet)
        log.warning(
            "deception: canary %r triggered from %s",
            trigger.get("canary_id"),
            trigger.get("requestor_ip"),
        )

    def get_honeypot_routes(self) -> list[dict]:
        """Return honeypot spec list for HubAPI to register as fake routes."""
        return list(self._honeypot_specs)
