from __future__ import annotations

import asyncio
import hashlib
import logging
import random
from typing import TYPE_CHECKING

import httpx

from ..base import BaseNode
from ._feed_entry import entry_published, entry_text, entry_title, entry_url
from ...spine.packet import IntelPacket, TrustLevel

if TYPE_CHECKING:
    from ...nexus.spec import NodeSpec
    from ...spine.spine import Spine

log = logging.getLogger(__name__)

SEEN_HASHES_CAP = 50_000


class FeedNode(BaseNode):
    """Ingests RSS/Atom feeds and emits RawFeedPackets."""

    def __init__(self, spec: "NodeSpec", spine: "Spine") -> None:
        super().__init__(spec, spine)
        self._seen_hashes: dict[str, None] = {}  # insertion-ordered, oldest first
        self._feed_urls: list[str] = []
        self._interval_seconds: int = 900
        self._keywords: list[str] = []
        self._exclude_keywords: list[str] = []
        self._min_word_count: int = 0
        self._client: httpx.AsyncClient | None = None

    async def setup(self) -> None:
        self._feed_urls = [t["url"] for t in self.spec.targets if t.get("type") == "feed"]
        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=30,
            trust_env=True,
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

        if self._cb_check(url):
            log.debug("node %s: circuit open for %s — skipping", self.node_id, url)
            return

        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            raw = resp.content
        except Exception as e:
            await self._handle_fetch_error(url, e)
            return

        try:
            feed = atoma.parse_rss_bytes(raw)
            entries = feed.items
        except Exception:
            try:
                feed = atoma.parse_atom_bytes(raw)
                entries = feed.entries
            except Exception as e:
                await self._handle_fetch_error(url, e)
                return

        self._cb_success(url)

        for entry in entries:
            content = entry_text(entry)
            if not self._passes_filters(content):
                continue

            content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
            if content_hash in self._seen_hashes:
                continue
            self._seen_hashes[content_hash] = None
            if len(self._seen_hashes) > SEEN_HASHES_CAP:
                for old in list(self._seen_hashes)[: SEEN_HASHES_CAP // 2]:
                    del self._seen_hashes[old]

            packet = IntelPacket(
                packet_type="RawFeedPacket",
                source_node_id=self.node_id,
                apparatus_id=self.apparatus_id,
                channel=self._emit_channel,
                payload={
                    "title": entry_title(entry),
                    "url": entry_url(entry),
                    "content": content,
                    "published": entry_published(entry),
                    "feed_url": url,
                    "tags": [],
                },
                priority=self._emit_priority,
                trust_level=TrustLevel.UNCLASSIFIED,
                source_hash=content_hash,
            )
            if self._emit_channel:
                await self.emit(self._emit_channel, packet)
            log.debug("node %s emitted entry: %s", self.node_id, packet.payload["title"])

    def _passes_filters(self, text: str) -> bool:
        lower = text.lower()
        if self._min_word_count and len(text.split()) < self._min_word_count:
            return False
        if self._exclude_keywords and any(kw in lower for kw in self._exclude_keywords):
            return False
        if self._keywords and not any(kw in lower for kw in self._keywords):
            return False
        return True
