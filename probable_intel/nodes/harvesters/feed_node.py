from __future__ import annotations

import asyncio
import hashlib
import logging
import random
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import httpx

from ..base import BaseNode
from ...spine.packet import IntelPacket, Priority, TrustLevel

if TYPE_CHECKING:
    from ...nexus.spec import NodeSpec
    from ...spine.spine import Spine

log = logging.getLogger(__name__)


class FeedNode(BaseNode):
    """Ingests RSS/Atom feeds and emits RawFeedPackets."""

    def __init__(self, spec: "NodeSpec", spine: "Spine") -> None:
        super().__init__(spec, spine)
        self._seen_hashes: set[str] = set()
        self._feed_urls: list[str] = []
        self._interval_seconds: int = 900
        self._keywords: list[str] = []
        self._exclude_keywords: list[str] = []
        self._min_word_count: int = 0
        self._emit_channel: str = ""
        self._emit_priority: Priority = Priority.NORMAL
        self._client: httpx.AsyncClient | None = None

    async def setup(self) -> None:
        self._feed_urls = [t["url"] for t in self.spec.targets if t.get("type") == "feed"]
        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=30,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) "
                    "Gecko/20100101 Firefox/128.0"
                ),
                "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, */*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Cache-Control": "no-cache",
            },
        )
        if not self._feed_urls:
            log.warning("node %s: no feed targets configured", self.node_id)

        if self.spec.schedule and self.spec.schedule.interval_seconds:
            self._interval_seconds = self.spec.schedule.interval_seconds

        filters = self.spec.filters
        self._keywords = [kw.lower() for kw in filters.get("keywords", [])]
        self._exclude_keywords = [kw.lower() for kw in filters.get("exclude_keywords", [])]
        self._min_word_count = filters.get("min_word_count", 0)

        if self.spec.emit:
            self._emit_channel = self.spec.emit.channel
            self._emit_priority = Priority[self.spec.emit.priority.upper()]

    async def teardown(self) -> None:
        if self._client:
            await self._client.aclose()

    async def run(self) -> None:
        for url in self._feed_urls:
            await self._fetch_feed(url)

        jitter = 0
        if self.spec.schedule and self.spec.schedule.jitter_seconds:
            jitter = random.randint(0, self.spec.schedule.jitter_seconds)
        await asyncio.sleep(self._interval_seconds + jitter)

    async def _fetch_feed(self, url: str) -> None:
        import atoma

        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            raw = resp.content
        except Exception as e:
            log.error("node %s: failed to fetch %s: %s", self.node_id, url, e)
            return

        try:
            feed = atoma.parse_rss_bytes(raw)
            entries = feed.items
        except Exception:
            try:
                feed = atoma.parse_atom_bytes(raw)
                entries = feed.entries
            except Exception as e:
                log.error("node %s: failed to parse feed %s: %s", self.node_id, url, e)
                return

        for entry in entries:
            content = self._extract_content(entry)
            if not self._passes_filters(content):
                continue

            content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
            if content_hash in self._seen_hashes:
                continue
            self._seen_hashes.add(content_hash)
            # Cap memory footprint
            if len(self._seen_hashes) > 50_000:
                self._seen_hashes = set(list(self._seen_hashes)[-25_000:])

            packet = IntelPacket(
                packet_type="RawFeedPacket",
                source_node_id=self.node_id,
                apparatus_id=self.apparatus_id,
                channel=self._emit_channel,
                payload={
                    "title": getattr(entry, "title", "") or "",
                    "url": str(getattr(entry, "url", "") or getattr(entry, "id_", "") or ""),
                    "content": content,
                    "published": self._parse_date(entry),
                    "feed_url": url,
                    "tags": [],
                },
                priority=self._emit_priority,
                trust_level=TrustLevel.UNCLASSIFIED,
                source_hash=content_hash,
            )
            if self._emit_channel:
                await self.emit(self._emit_channel, packet)
            log.debug("node %s emitted entry: %s", self.node_id, getattr(entry, "title", "?"))

    def _extract_content(self, entry: object) -> str:
        for attr in ("description", "summary", "content"):
            val = getattr(entry, attr, None)
            if val:
                if isinstance(val, list):
                    return str(val[0]) if val else ""
                return str(val)
        return ""

    def _passes_filters(self, text: str) -> bool:
        lower = text.lower()
        if self._min_word_count and len(text.split()) < self._min_word_count:
            return False
        if self._exclude_keywords and any(kw in lower for kw in self._exclude_keywords):
            return False
        if self._keywords and not any(kw in lower for kw in self._keywords):
            return False
        return True

    def _parse_date(self, entry: object) -> str:
        for attr in ("pub_date", "updated", "published", "created"):
            val = getattr(entry, attr, None)
            if val:
                return str(val)
        return datetime.now(timezone.utc).isoformat()
