from __future__ import annotations

import asyncio
import hashlib
import logging
import random
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import httpx

from ..base import BaseNode
from ...spine.packet import IntelPacket, TrustLevel

if TYPE_CHECKING:
    from ...nexus.spec import NodeSpec
    from ...spine.spine import Spine

log = logging.getLogger(__name__)

_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) "
    "Gecko/20100101 Firefox/128.0"
)


class SocialNode(BaseNode):
    """Multi-source social signal collector. Emits RawSocialPackets.

    Supported source types: reddit, hackernews, mastodon
    """

    def __init__(self, spec: "NodeSpec", spine: "Spine") -> None:
        super().__init__(spec, spine)
        self._targets: list[dict[str, Any]] = []
        self._interval_seconds: int = 1200
        self._keywords: list[str] = []
        self._exclude_keywords: list[str] = []
        self._min_word_count: int = 0
        self._client: httpx.AsyncClient | None = None
        self._seen_urls: set[str] = set()

    async def setup(self) -> None:
        self._targets = [t for t in self.spec.targets if t.get("type") == "social"]

        if not self._targets:
            log.warning("node %s: no social targets configured", self.node_id)

        if self.spec.schedule and self.spec.schedule.interval_seconds:
            self._interval_seconds = self.spec.schedule.interval_seconds

        filters = self.spec.filters
        self._keywords = [kw.lower() for kw in filters.get("keywords", [])]
        self._exclude_keywords = [kw.lower() for kw in filters.get("exclude_keywords", [])]
        self._min_word_count = filters.get("min_word_count", 0)

        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=30,
            trust_env=True,
            headers={
                "User-Agent": _BROWSER_UA,
                "Accept": "application/json, */*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            },
        )

    async def teardown(self) -> None:
        if self._client:
            await self._client.aclose()

    async def run(self) -> None:
        for target in self._targets:
            source = target.get("source", "")
            try:
                if source == "reddit":
                    await self._fetch_reddit(target)
                elif source == "hackernews":
                    await self._fetch_hackernews(target)
                elif source == "mastodon":
                    await self._fetch_mastodon(target)
                else:
                    log.warning("node %s: unknown social source %r", self.node_id, source)
            except Exception as e:
                log.error("node %s: error fetching %s: %s", self.node_id, source, e)

        jitter = 0
        if self.spec.schedule and self.spec.schedule.jitter_seconds:
            jitter = random.randint(0, self.spec.schedule.jitter_seconds)
        await asyncio.sleep(self._interval_seconds + jitter)

    # ── Reddit ────────────────────────────────────────────────────────────────

    async def _fetch_reddit(self, target: dict[str, Any]) -> None:
        """Reddit's public JSON API requires OAuth since June 2023.

        Use FeedNode with https://www.reddit.com/r/<subreddit>/.rss instead,
        which works without authentication. This method is kept for backwards
        compatibility but logs a one-time warning and is a no-op.
        """
        subreddit = target.get("subreddit", "")
        log.warning(
            "node %s: Reddit JSON API requires OAuth (disabled since 2023). "
            "Use FeedNode with target feed: https://www.reddit.com/r/%s/.rss instead.",
            self.node_id, subreddit or "<subreddit>",
        )

    # ── HackerNews ────────────────────────────────────────────────────────────

    async def _fetch_hackernews(self, target: dict[str, Any]) -> None:
        query = target.get("query", "security vulnerability exploit")
        url = "https://hn.algolia.com/api/v1/search"
        params = {"query": query, "tags": "story", "hitsPerPage": 20}

        try:
            resp = await self._client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.error("node %s: HackerNews fetch failed: %s", self.node_id, e)
            return

        for hit in data.get("hits", []):
            hn_url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
            title = hit.get("title", "")
            content = f"{title}\n{hit.get('story_text', '')}".strip()
            created_at = hit.get("created_at", "")

            await self._maybe_emit(
                url=hn_url,
                title=title,
                content=content,
                author=hit.get("author", ""),
                source="hackernews",
                extra={
                    "score": hit.get("points", 0),
                    "published": created_at,
                    "hn_id": hit.get("objectID", ""),
                },
            )

    # ── Mastodon ──────────────────────────────────────────────────────────────

    async def _fetch_mastodon(self, target: dict[str, Any]) -> None:
        instance = target.get("instance", "mastodon.social")
        hashtag = target.get("hashtag", "")

        if hashtag:
            url = f"https://{instance}/api/v1/timelines/tag/{hashtag}?limit=20"
        else:
            url = f"https://{instance}/api/v1/timelines/public?limit=20&local=true"

        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            statuses = resp.json()
        except Exception as e:
            log.error("node %s: Mastodon fetch failed %s: %s", self.node_id, instance, e)
            return

        for status in statuses:
            post_url = status.get("url", "")
            content_html = status.get("content", "")
            # Strip basic HTML tags
            content = content_html.replace("<p>", " ").replace("</p>", " ")
            for tag in ("<br>", "<br/>", "<br />", "</a>", "</span>"):
                content = content.replace(tag, " ")
            import re
            content = re.sub(r"<[^>]+>", "", content).strip()

            account = status.get("account", {})
            author = f"@{account.get('acct', 'unknown')}"
            published = status.get("created_at", "")
            boosts = status.get("reblogs_count", 0)

            await self._maybe_emit(
                url=post_url,
                title=content[:100],
                content=content,
                author=author,
                source=f"mastodon:{instance}",
                extra={"score": boosts, "published": published},
            )

    # ── Shared emit ───────────────────────────────────────────────────────────

    async def _maybe_emit(
        self,
        *,
        url: str,
        title: str,
        content: str,
        author: str,
        source: str,
        extra: dict[str, Any],
    ) -> None:
        if not url:
            return

        dedup_key = hashlib.sha256(url.encode()).hexdigest()[:16]
        if dedup_key in self._seen_urls:
            return
        self._seen_urls.add(dedup_key)
        if len(self._seen_urls) > 50_000:
            self._seen_urls = set(list(self._seen_urls)[-25_000:])

        combined = f"{title} {content}".lower()
        if self._min_word_count and len(content.split()) < self._min_word_count:
            return
        if self._keywords and not any(kw in combined for kw in self._keywords):
            return
        if self._exclude_keywords and any(kw in combined for kw in self._exclude_keywords):
            return

        if not self._emit_channel:
            return

        payload: dict[str, Any] = {
            "title": title,
            "content": content,
            "url": url,
            "author": author,
            "source": source,
            **extra,
        }

        packet = IntelPacket(
            packet_type="RawSocialPacket",
            source_node_id=self.node_id,
            apparatus_id=self.apparatus_id,
            channel=self._emit_channel,
            payload=payload,
            priority=self._emit_priority,
            trust_level=TrustLevel.UNCLASSIFIED,
            source_hash=dedup_key,
        )
        await self.emit(self._emit_channel, packet)
        log.debug("node %s emitted social post from %s: %s", self.node_id, source, url)
