from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from typing import TYPE_CHECKING

from ..base import BaseNode
from ...spine.packet import IntelPacket, Priority, TrustLevel

if TYPE_CHECKING:
    from ...nexus.spec import NodeSpec
    from ...spine.spine import Spine

log = logging.getLogger(__name__)

_AUDIT_CHANNEL = "ci.opsec.audit"


class FingerprintDefenseNode(BaseNode):
    """Injects timing jitter and tracks identity usage for all harvester requests.

    Harvester nodes call report_request() before each outbound HTTP request.
    This node audits the call, applies jitter, and publishes to the opsec audit channel.
    """

    HEARTBEAT_INTERVAL = 30.0

    def __init__(self, spec: "NodeSpec", spine: "Spine") -> None:
        super().__init__(spec, spine)
        self._jitter_mu: float = 2.1
        self._jitter_sigma: float = 0.8
        self._request_queue: asyncio.Queue = asyncio.Queue()

    async def setup(self) -> None:
        config = self.spec.config
        self._jitter_mu = float(config.get("jitter_mu", 2.1))
        self._jitter_sigma = float(config.get("jitter_sigma", 0.8))
        log.info("node %s: fingerprint defense active (μ=%.1fs)", self.node_id, self._jitter_mu)

    async def run(self) -> None:
        try:
            req_info = await asyncio.wait_for(self._request_queue.get(), timeout=5.0)
        except asyncio.TimeoutError:
            return
        await self._process_request(req_info)

    async def report_request(
        self,
        identity_profile_id: str,
        target_url: str,
        node_id: str,
    ) -> float:
        """Called by harvesters before each request. Returns jitter delay seconds."""
        delay = self._lognormal_delay()
        info = {
            "identity_profile_id": identity_profile_id,
            "target_url": target_url,
            "requesting_node": node_id,
            "jitter_applied": delay,
            "timestamp": time.time(),
        }
        await self._request_queue.put(info)
        return delay

    def _lognormal_delay(self) -> float:
        """Sample inter-request delay from LogNormal distribution."""
        raw = random.lognormvariate(
            math.log(self._jitter_mu),
            self._jitter_sigma,
        )
        return max(0.5, min(raw, 30.0))

    async def _process_request(self, info: dict) -> None:
        packet = IntelPacket(
            packet_type="AuditRequestEvent",
            source_node_id=self.node_id,
            apparatus_id=self.apparatus_id,
            channel=_AUDIT_CHANNEL,
            payload={"event_type": "request", **info},
            priority=Priority.LOW,
            trust_level=TrustLevel.CLASSIFIED,
            ttl_seconds=3600,
        )
        await self.emit(_AUDIT_CHANNEL, packet)
