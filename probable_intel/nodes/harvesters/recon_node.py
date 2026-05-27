from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import quote_plus

import httpx

from ..base import BaseNode
from ...spine.packet import IntelPacket, TrustLevel

if TYPE_CHECKING:
    from ...nexus.spec import NodeSpec
    from ...spine.spine import Spine

log = logging.getLogger(__name__)

_GNEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) "
    "Gecko/20100101 Firefox/128.0"
)


class ReconNode(BaseNode):
    """Autonomous OSINT expansion — follows KG entity leads to new sources.

    Subscribes to KnowledgeGraphNode summary packets. When high-value entities
    cross a degree/count threshold, issues targeted Google News RSS searches
    and re-injects discovered articles into the pipeline as RawReconPackets.

    This closes the autonomous collection loop: the KG surfaces what matters,
    ReconNode goes out to find more about it, without human intervention.

    Config keys:
        min_entity_degree     Only recon entities with this many KG connections (default 2)
        max_entities_per_run  Rate limit: entities searched per cycle (default 5)
        cooldown_hours        Skip re-searching an entity within this window (default 6)
        feed_node_id          If set, inject discovered URLs into this FeedNode at runtime
    """

    def __init__(self, spec: "NodeSpec", spine: "Spine") -> None:
        super().__init__(spec, spine)
        self._subscriptions: list = []
        self._min_degree: int = 2
        self._max_per_run: int = 5
        self._cooldown_seconds: float = 6 * 3600
        self._feed_node_id: str = ""
        self._client: httpx.AsyncClient | None = None
        # Ordered dict used as an insertion-order set for LRU dedup pruning
        self._seen_urls: dict[str, None] = {}
        self._last_searched: dict[str, float] = {}  # entity_key → timestamp
        self._atoma_ok: bool = True

    async def setup(self) -> None:
        cfg = self.spec.config
        self._min_degree = int(cfg.get("min_entity_degree", 2))
        self._max_per_run = int(cfg.get("max_entities_per_run", 5))
        self._cooldown_seconds = float(cfg.get("cooldown_hours", 6)) * 3600
        self._feed_node_id = str(cfg.get("feed_node_id", ""))

        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=20.0,
            trust_env=True,
            headers={
                "User-Agent": _BROWSER_UA,
                "Accept": "application/rss+xml, application/xml, */*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            },
        )
        try:
            import atoma  # noqa: F401
        except ImportError:
            self._atoma_ok = False
            log.warning(
                "node %s: 'atoma' not installed; ReconNode will produce no output. "
                "Install with: pip install atoma",
                self.node_id,
            )

        self._subscriptions = [
            self.spine.subscribe(ch) for ch in self.spec.subscribe_channels
        ]
        log.info(
            "node %s: ReconNode ready (min_degree=%d, max_per_run=%d, cooldown=%.0fh)",
            self.node_id, self._min_degree, self._max_per_run,
            self._cooldown_seconds / 3600,
        )

    async def teardown(self) -> None:
        for sub in self._subscriptions:
            sub.close()
        if self._client:
            await self._client.aclose()

    async def run(self) -> None:
        packet = await self._wait_any(self._subscriptions)
        if packet is None:
            return
        await self._process_summary(packet)

    async def _process_summary(self, packet: IntelPacket) -> None:
        payload = packet.payload
        top_entities: list[dict] = payload.get("top_entities", [])
        if not top_entities:
            return

        now = time.time()
        candidates = [
            e for e in top_entities
            if e.get("degree", 0) >= self._min_degree
            and (now - self._last_searched.get(e.get("key", ""), 0)) >= self._cooldown_seconds
        ]

        if not candidates:
            return

        searched = 0
        for entity in candidates[:self._max_per_run]:
            key = entity.get("key", "")
            text = entity.get("text", "")
            etype = entity.get("type", "")
            if not text:
                continue

            query = self._build_query(text, etype)
            count = await self._search_and_emit(query, entity, packet)
            self._last_searched[key] = now
            searched += 1
            log.info(
                "node %s: recon %r → %d new articles (degree=%d)",
                self.node_id, text, count, entity.get("degree", 0),
            )
            await asyncio.sleep(1.0)  # polite delay between searches

        if searched:
            log.info("node %s: recon cycle complete — searched %d entities", self.node_id, searched)

    def _build_query(self, text: str, etype: str) -> str:
        type_context = {
            "CVE": "vulnerability exploit CVE",
            "ORG": "threat actor cyberattack",
            "GPE": "cyber attack nation state",
            "PRODUCT": "vulnerability security flaw",
            "PERSON": "hacker threat actor security",
        }
        suffix = type_context.get(etype, "cybersecurity threat intelligence")
        return f"{text} {suffix}"

    async def _search_and_emit(
        self, query: str, entity: dict[str, Any], trigger: IntelPacket
    ) -> int:
        url = _GNEWS_RSS.format(query=quote_plus(query))
        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
            return await self._parse_and_emit(resp.content, entity, trigger)
        except Exception as e:
            log.debug("node %s: recon search failed for %r: %s", self.node_id, query, e)
            return 0

    async def _parse_and_emit(
        self, content: bytes, entity: dict[str, Any], trigger: IntelPacket
    ) -> int:
        if not self._atoma_ok:
            return 0
        import atoma
        try:
            feed = atoma.parse_rss_bytes(content)
            items = feed.items
        except Exception:
            try:
                feed = atoma.parse_atom_bytes(content)
                items = feed.entries
            except Exception as e:
                log.debug("node %s: recon feed parse failed: %s", self.node_id, e)
                return 0

        count = 0
        for item in items:
            try:
                item_url = str(getattr(item, "link", None) or getattr(item, "id_", "") or "")
                if not item_url:
                    continue
                dedup = hashlib.sha256(item_url.encode()).hexdigest()[:16]
                if dedup in self._seen_urls:
                    continue
                self._seen_urls[dedup] = None
                if len(self._seen_urls) > 100_000:
                    # Prune oldest 50k — dict preserves insertion order (Python 3.7+)
                    oldest = list(self._seen_urls.keys())[:50_000]
                    for k in oldest:
                        del self._seen_urls[k]

                title = str(getattr(item, "title", None) or "")
                description = str(getattr(item, "summary", None) or getattr(item, "content", None) or "")
                content_text = f"{title}. {description}".strip()

                if not self._emit_channel:
                    continue

                out = IntelPacket(
                    packet_type="RawReconPacket",
                    source_node_id=self.node_id,
                    apparatus_id=self.apparatus_id,
                    channel=self._emit_channel,
                    payload={
                        "title": title,
                        "content": content_text,
                        "url": item_url,
                        "source": "google-news-recon",
                        "recon_entity": entity.get("key", ""),
                        "recon_entity_type": entity.get("type", ""),
                        "recon_entity_text": entity.get("text", ""),
                    },
                    priority=self._emit_priority,
                    trust_level=TrustLevel.UNCLASSIFIED,
                    source_hash=dedup,
                    tags=["recon", f"entity:{entity.get('key', '')}"],
                )
                out.provenance = [self.node_id] + list(trigger.provenance)
                await self.emit(self._emit_channel, out)
                count += 1
            except Exception as e:
                log.debug("node %s: recon item emit failed: %s", self.node_id, e)

        return count
