from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from ..base import BaseNode
from ...spine.packet import IntelPacket

if TYPE_CHECKING:
    from ...nexus.spec import NodeSpec
    from ...spine.spine import Spine

log = logging.getLogger(__name__)


class AlertNode(BaseNode):
    """Terminal node — routes ThreatPackets to webhook or logfile sinks."""

    def __init__(self, spec: "NodeSpec", spine: "Spine") -> None:
        super().__init__(spec, spine)
        self._subscriptions = []
        self._webhook_url: str = ""
        self._log_path: Path | None = None
        self._severity_filter: set[str] = {"HIGH", "CRITICAL"}
        self._dedup_window: int = 1800
        self._recent_hashes: dict[str, float] = {}
        self._client: httpx.AsyncClient | None = None

    async def setup(self) -> None:
        config = self.spec.config
        self._webhook_url = config.get("webhook_url", "")
        log_path = config.get("log_path", "")
        if log_path:
            self._log_path = Path(log_path)
            self._log_path.parent.mkdir(parents=True, exist_ok=True)

        self._dedup_window = config.get("dedup_window_seconds", 1800)

        triggers = config.get("triggers", [])
        if triggers:
            for trigger in triggers:
                sevs = trigger.get("severity", [])
                output = trigger.get("output", "logfile")
                if output == "webhook":
                    self._webhook_url = trigger.get("webhook_url", self._webhook_url)
                elif output == "logfile":
                    path = trigger.get("path", "")
                    if path:
                        self._log_path = Path(path)
                        self._log_path.parent.mkdir(parents=True, exist_ok=True)
                self._severity_filter.update(s.upper() for s in sevs)

        if self._webhook_url:
            self._client = httpx.AsyncClient(timeout=10)

        self._subscriptions = [
            self.spine.subscribe(ch) for ch in self.spec.subscribe_channels
        ]

    async def teardown(self) -> None:
        for sub in self._subscriptions:
            sub.close()
        if self._client:
            await self._client.aclose()

    async def run(self) -> None:
        if not self._subscriptions:
            await asyncio.sleep(1)
            return

        tasks = [asyncio.create_task(sub.get()) for sub in self._subscriptions]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()

        packet: IntelPacket = next(iter(done)).result()
        await self._handle(packet)

    async def _handle(self, packet: IntelPacket) -> None:
        severity = packet.payload.get("max_severity", "LOW")
        if severity not in self._severity_filter:
            return

        # dedup
        key = packet.source_hash
        now = time.time()
        if key in self._recent_hashes and (now - self._recent_hashes[key]) < self._dedup_window:
            return
        self._recent_hashes[key] = now
        # prune old entries
        cutoff = now - self._dedup_window
        self._recent_hashes = {k: v for k, v in self._recent_hashes.items() if v > cutoff}

        alert = self._format_alert(packet, severity)

        if self._log_path:
            await self._write_log(alert)
        if self._webhook_url and self._client:
            await self._send_webhook(alert)

    def _format_alert(self, packet: IntelPacket, severity: str) -> dict:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "severity": severity,
            "apparatus": packet.apparatus_id,
            "source_node": packet.source_node_id,
            "packet_id": str(packet.packet_id),
            "channel": packet.channel,
            "matches": packet.payload.get("threat_matches", []),
            "title": packet.payload.get("title", ""),
            "url": packet.payload.get("url", ""),
        }

    async def _write_log(self, alert: dict) -> None:
        line = json.dumps(alert) + "\n"
        try:
            assert self._log_path is not None
            with self._log_path.open("a") as f:
                f.write(line)
        except OSError as e:
            log.error("node %s: failed to write alert log: %s", self.node_id, e)
        else:
            log.info("node %s: alert written [%s]", self.node_id, alert["severity"])

    async def _send_webhook(self, alert: dict) -> None:
        try:
            assert self._client is not None
            resp = await self._client.post(self._webhook_url, json=alert)
            resp.raise_for_status()
        except Exception as e:
            log.error("node %s: webhook delivery failed: %s", self.node_id, e)
