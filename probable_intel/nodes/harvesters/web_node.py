from __future__ import annotations

import asyncio
import hashlib
import logging
import random
from typing import TYPE_CHECKING

import httpx

from ..base import BaseNode
from ...spine.packet import IntelPacket, Priority, TrustLevel

if TYPE_CHECKING:
    from ...nexus.spec import NodeSpec
    from ...spine.spine import Spine

log = logging.getLogger(__name__)

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class WebNode(BaseNode):
    """HTTP scraper — httpx-based. Playwright upgrade wires in via FingerprintDefenseNode."""

    def __init__(self, spec: "NodeSpec", spine: "Spine") -> None:
        super().__init__(spec, spine)
        self._targets: list[dict] = []
        self._interval_seconds: int = 3600
        self._emit_channel: str = ""
        self._emit_priority: Priority = Priority.NORMAL
        self._seen: set[str] = set()
        self._client: httpx.AsyncClient | None = None

    async def setup(self) -> None:
        self._targets = [t for t in self.spec.targets if t.get("type") == "web"]
        if self.spec.schedule and self.spec.schedule.interval_seconds:
            self._interval_seconds = self.spec.schedule.interval_seconds
        if self.spec.emit:
            self._emit_channel = self.spec.emit.channel
            self._emit_priority = Priority[self.spec.emit.priority.upper()]
        self._client = httpx.AsyncClient(
            headers=_DEFAULT_HEADERS,
            follow_redirects=True,
            timeout=30,
            trust_env=True,
        )

    async def teardown(self) -> None:
        if self._client:
            await self._client.aclose()

    async def run(self) -> None:
        for target in self._targets:
            await self._fetch(target)
        jitter = 0
        if self.spec.schedule and self.spec.schedule.jitter_seconds:
            jitter = random.randint(0, self.spec.schedule.jitter_seconds)
        await asyncio.sleep(self._interval_seconds + jitter)

    async def _fetch(self, target: dict) -> None:
        url = target["url"]
        try:
            assert self._client is not None
            resp = await self._client.get(url)
            resp.raise_for_status()
        except Exception as e:
            log.warning("node %s: fetch failed %s: %s", self.node_id, url, e)
            return

        content_hash = hashlib.sha256(resp.content).hexdigest()[:16]
        if content_hash in self._seen:
            return
        self._seen.add(content_hash)

        packet = IntelPacket(
            packet_type="RawWebPacket",
            source_node_id=self.node_id,
            apparatus_id=self.apparatus_id,
            channel=self._emit_channel,
            payload={
                "url": url,
                "status_code": resp.status_code,
                "content_type": resp.headers.get("content-type", ""),
                "body": resp.text[:50_000],
            },
            priority=self._emit_priority,
            trust_level=TrustLevel.UNCLASSIFIED,
            source_hash=content_hash,
        )
        if self._emit_channel:
            await self.emit(self._emit_channel, packet)
        log.debug("node %s fetched %s", self.node_id, url)
