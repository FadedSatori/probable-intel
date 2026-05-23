from __future__ import annotations

import asyncio
import hashlib
import logging
import random
from typing import TYPE_CHECKING, Any

import httpx

from ..base import BaseNode
from ...spine.packet import IntelPacket, Priority, TrustLevel

if TYPE_CHECKING:
    from ...nexus.spec import NodeSpec
    from ...spine.spine import Spine

log = logging.getLogger(__name__)

# Built-in presets for common intel APIs
_PRESETS: dict[str, dict[str, Any]] = {
    "nvd": {
        "url": "https://services.nvd.nist.gov/rest/json/cves/2.0",
        "params": {"resultsPerPage": 20},
        "auth_header": "apiKey",
        "response_path": "vulnerabilities",
        "id_field": "cve.id",
        "title_field": "cve.id",
        "content_field": "cve.descriptions.0.value",
    },
    "circl": {
        "url": "https://cve.circl.lu/api/last/20",
        "response_path": "",  # top-level list
        "id_field": "id",
        "title_field": "id",
        "content_field": "summary",
    },
    "greynoise": {
        "url": "https://api.greynoise.io/v2/experimental/gnql",
        "params": {"query": "classification:malicious", "size": 20},
        "auth_header": "key",
        "response_path": "data",
        "id_field": "ip",
        "title_field": "ip",
        "content_field": "metadata.organization",
    },
}


def _dig(obj: Any, path: str) -> Any:
    """Traverse a dot-notation path through nested dicts/lists. Returns None if not found."""
    if not path:
        return obj
    for part in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(part)
        elif isinstance(obj, list):
            try:
                obj = obj[int(part)]
            except (IndexError, ValueError):
                return None
        else:
            return None
    return obj


class ApiNode(BaseNode):
    """Authenticated JSON REST collector. Emits RawApiPackets."""

    def __init__(self, spec: "NodeSpec", spine: "Spine") -> None:
        super().__init__(spec, spine)
        self._targets: list[dict[str, Any]] = []
        self._interval_seconds: int = 3600
        self._keywords: list[str] = []
        self._exclude_keywords: list[str] = []
        self._emit_channel: str = ""
        self._emit_priority: Priority = Priority.NORMAL
        self._client: httpx.AsyncClient | None = None
        self._seen_ids: set[str] = set()

    async def setup(self) -> None:
        raw_targets = [t for t in self.spec.targets if t.get("type") == "api"]
        self._targets = [self._resolve_target(t) for t in raw_targets]

        if not self._targets:
            log.warning("node %s: no api targets configured", self.node_id)

        if self.spec.schedule and self.spec.schedule.interval_seconds:
            self._interval_seconds = self.spec.schedule.interval_seconds

        filters = self.spec.filters
        self._keywords = [kw.lower() for kw in filters.get("keywords", [])]
        self._exclude_keywords = [kw.lower() for kw in filters.get("exclude_keywords", [])]

        if self.spec.emit:
            self._emit_channel = self.spec.emit.channel
            self._emit_priority = Priority[self.spec.emit.priority.upper()]

        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=30,
            headers={"User-Agent": "probable-intel/0.1 api-collector"},
        )

    def _resolve_target(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Merge preset defaults with per-target overrides."""
        preset_name = raw.get("preset", "")
        base = dict(_PRESETS.get(preset_name, {}))
        # Per-target values override preset
        for k, v in raw.items():
            if k not in ("type", "preset"):
                base[k] = v
        if not base.get("url"):
            base["url"] = raw.get("url", "")
        return base

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

    async def _fetch(self, target: dict[str, Any]) -> None:
        url: str = target.get("url", "")
        if not url:
            return

        params = dict(target.get("params", {}))
        headers: dict[str, str] = {}

        auth_env = target.get("auth_env", "")
        if auth_env:
            from ...hub.secrets import SecretManager
            secret = SecretManager().get(auth_env, default="")
            if secret:
                auth_header = target.get("auth_header", "Authorization")
                if auth_header.lower() == "authorization":
                    headers["Authorization"] = f"Bearer {secret}"
                else:
                    headers[auth_header] = secret
            else:
                log.debug("node %s: auth_env %r not set; proceeding unauthenticated", self.node_id, auth_env)

        try:
            resp = await self._client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.error("node %s: API fetch failed %s: %s", self.node_id, url, e)
            return

        response_path = target.get("response_path", "")
        items = _dig(data, response_path) if response_path else data
        if not isinstance(items, list):
            items = [data]

        id_field = target.get("id_field", "id")
        title_field = target.get("title_field", "title")
        content_field = target.get("content_field", "summary")
        source_label = target.get("preset", "") or url.split("/")[2] if "://" in url else url

        for item in items:
            await self._emit_item(item, url, id_field, title_field, content_field, source_label)

    async def _emit_item(
        self,
        item: Any,
        source_url: str,
        id_field: str,
        title_field: str,
        content_field: str,
        source_label: str,
    ) -> None:
        if not isinstance(item, dict):
            return

        item_id = str(_dig(item, id_field) or "")
        title = str(_dig(item, title_field) or "")
        content = str(_dig(item, content_field) or "")

        if not item_id and not content:
            return

        dedup_key = hashlib.sha256((item_id or content).encode()).hexdigest()[:16]
        if dedup_key in self._seen_ids:
            return
        self._seen_ids.add(dedup_key)
        if len(self._seen_ids) > 50_000:
            self._seen_ids = set(list(self._seen_ids)[-25_000:])

        combined = f"{title} {content}".lower()
        if self._keywords and not any(kw in combined for kw in self._keywords):
            return
        if self._exclude_keywords and any(kw in combined for kw in self._exclude_keywords):
            return

        if not self._emit_channel:
            return

        packet = IntelPacket(
            packet_type="RawApiPacket",
            source_node_id=self.node_id,
            apparatus_id=self.apparatus_id,
            channel=self._emit_channel,
            payload={
                "title": title,
                "content": content,
                "url": source_url,
                "item_id": item_id,
                "source": source_label,
                "raw": item,
            },
            priority=self._emit_priority,
            trust_level=TrustLevel.UNCLASSIFIED,
            source_hash=dedup_key,
        )
        await self.emit(self._emit_channel, packet)
        log.debug("node %s emitted API item: %s", self.node_id, item_id or title)
