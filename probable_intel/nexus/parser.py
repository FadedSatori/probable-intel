"""NEXUS DSL parser — YAML substrate with NEXUS schema enforcement."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from .errors import NEXUSError
from .spec import (
    ApparatusSpec, EmitSpec, FederationPeerSpec, FederationSpec, HoneypotSpec, LLMSpec,
    NodeSpec, RouterSpec, RuleSpec, ScheduleSpec, StorageSpec,
)

_DURATION_RE = re.compile(r"^(\d+)([smhd])$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

_VALID_NODE_TYPES = {
    "WebNode", "FeedNode", "SocialNode", "ApiNode",
    "SentimentNode", "EntityExtractorNode", "ThreatAssessNode", "NarrativeNode",
    "MonitorNode", "AnomalyNode", "AlertNode",
    "StorageNode", "KnowledgeGraphNode",
    "TaskRouterNode",
    "OpSecNode", "DeceptionNode", "FingerprintDefenseNode", "AttributionNode",
}

_VALID_TRUST_LEVELS = {"unclassified", "restricted", "classified", "top_secret"}
_VALID_PRIORITIES = {"low", "normal", "high", "critical"}


def _parse_duration(val: Any) -> int:
    """Parse '15m', '2h', '30s', '1d' → seconds. Accepts raw integers too."""
    if isinstance(val, int):
        return val
    s = str(val).strip()
    m = _DURATION_RE.match(s)
    if not m:
        raise ValueError(f"invalid duration {s!r}; expected format like '15m', '2h', '30s'")
    return int(m.group(1)) * _UNIT_SECONDS[m.group(2)]


class NexusParser:
    """Parses NEXUS apparatus files (.nx) — YAML format with NEXUS schema."""

    def parse_file(self, path: Path) -> ApparatusSpec:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            raise NEXUSError(str(e)) from e
        return self.parse(text)

    def parse(self, text: str) -> ApparatusSpec:
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise NEXUSError(f"YAML parse error: {e}") from e

        if not isinstance(data, dict):
            raise NEXUSError("apparatus file must be a YAML mapping at the top level")

        return self._build_apparatus(data)

    # ── apparatus ──────────────────────────────────────────────────────────

    def _build_apparatus(self, data: dict) -> ApparatusSpec:
        name = str(data.get("apparatus_name") or data.get("name", ""))
        if not name:
            raise NEXUSError(
                "apparatus must have 'apparatus_name' (or 'name') at the top level"
            )

        trust = str(data.get("trust_level", "unclassified"))
        if trust not in _VALID_TRUST_LEVELS:
            raise NEXUSError(
                f"invalid trust_level {trust!r}; must be one of {sorted(_VALID_TRUST_LEVELS)}",
                apparatus_name=name,
            )

        spec = ApparatusSpec(
            name=name,
            version=float(data.get("version", 1.0)),
            description=str(data.get("description", "")),
            trust_level=trust,
            owner=str(data.get("owner", "")),
        )

        for node_data in data.get("nodes", []):
            if isinstance(node_data, dict):
                spec.nodes.append(self._build_node(node_data, name))

        if "llm" in data and isinstance(data["llm"], dict):
            self._build_llm(spec.llm, data["llm"])
        if "storage" in data and isinstance(data["storage"], dict):
            self._build_storage(spec.storage, data["storage"])
        if "router" in data and isinstance(data["router"], dict):
            self._build_router(spec.router, data["router"])
        if "federation" in data and isinstance(data["federation"], dict):
            self._build_federation(spec.federation, data["federation"])

        return spec

    # ── node ───────────────────────────────────────────────────────────────

    def _build_node(self, data: dict, apparatus_id: str) -> NodeSpec:
        node_type = str(data.get("type", ""))
        node_id = str(data.get("id", ""))

        if not node_type:
            raise NEXUSError("node entry missing 'type'", apparatus_name=apparatus_id)
        if not node_id:
            raise NEXUSError("node entry missing 'id'", apparatus_name=apparatus_id)
        if node_type not in _VALID_NODE_TYPES:
            raise NEXUSError(
                f"unknown node type {node_type!r}",
                apparatus_name=apparatus_id,
            )

        spec = NodeSpec(
            node_type=node_type,
            node_id=node_id,
            apparatus_id=apparatus_id,
            config=data,
        )

        # targets
        for t in data.get("targets", []):
            if isinstance(t, dict):
                if "feed" in t:
                    spec.targets.append({"type": "feed", "url": str(t["feed"])})
                elif "url" in t:
                    spec.targets.append({"type": "web", "url": str(t["url"])})
                elif "api" in t:
                    spec.targets.append({"type": "api", "url": str(t["api"]),
                                         **{k: v for k, v in t.items() if k != "api"}})
                elif "source" in t:
                    spec.targets.append({"type": "social", **t})

        # subscribe
        sub = data.get("subscribe", {})
        if isinstance(sub, dict):
            spec.subscribe_channels = [str(c) for c in sub.get("channels", [])]
        elif isinstance(sub, list):
            spec.subscribe_channels = [str(c) for c in sub]

        # schedule
        sched = data.get("schedule")
        if sched and isinstance(sched, dict):
            s = ScheduleSpec()
            if "interval" in sched:
                s.interval_seconds = _parse_duration(sched["interval"])
            if "cron" in sched:
                s.cron = str(sched["cron"])
            if "jitter" in sched:
                s.jitter_seconds = int(sched["jitter"])
            spec.schedule = s

        # filters
        f = data.get("filters")
        if f and isinstance(f, dict):
            spec.filters = f

        # emit
        emit = data.get("emit")
        if emit and isinstance(emit, dict):
            ch = str(emit.get("channel", ""))
            pri = str(emit.get("priority", "normal")).lower()
            if pri not in _VALID_PRIORITIES:
                raise NEXUSError(
                    f"invalid priority {pri!r} in node {node_id!r}",
                    apparatus_name=apparatus_id,
                )
            spec.emit = EmitSpec(channel=ch, priority=pri)

        # rules
        for rule in data.get("rules", []):
            if isinstance(rule, dict):
                spec.rules.append(RuleSpec(
                    condition=str(rule.get("condition", "")),
                    severity=str(rule.get("severity", "LOW")).upper(),
                    label=str(rule.get("label", "")),
                ))

        # honeypots
        for hp in data.get("honeypots", []):
            if isinstance(hp, dict):
                spec.honeypots.append(HoneypotSpec(
                    type=str(hp.get("type", "")),
                    path=str(hp.get("path", "")),
                    canary_id=str(hp.get("canary_id", "")),
                    response=str(hp.get("response", "synthetic_json")),
                ))

        # identity
        identity = data.get("identity", {})
        if isinstance(identity, dict):
            spec.identity_profile = str(identity.get("profile", "rotate"))
            spec.proxy_pool = str(identity.get("proxy_pool", ""))

        # rate_limit
        rl = data.get("rate_limit", {})
        if isinstance(rl, dict) and "requests_per_minute" in rl:
            spec.rate_limit_rpm = int(rl["requests_per_minute"])

        # backend
        backend = data.get("backend", {})
        if isinstance(backend, dict):
            spec.backend = {str(k): str(v) for k, v in backend.items()}

        # per-node llm config
        node_llm = data.get("llm")
        if node_llm and isinstance(node_llm, dict):
            llm_spec = LLMSpec()
            self._build_llm(llm_spec, node_llm)
            spec.llm = llm_spec

        return spec

    # ── top-level blocks ────────────────────────────────────────────────────

    def _build_llm(self, llm: LLMSpec, data: dict) -> None:
        if "provider" in data:
            llm.provider = str(data["provider"])
        if "model" in data:
            llm.model = str(data["model"])
        if "api_key_env" in data:
            llm.api_key_env = str(data["api_key_env"])
        if "max_tokens" in data:
            llm.max_tokens = int(data["max_tokens"])
        if "budget_per_day_usd" in data:
            llm.budget_per_day_usd = float(data["budget_per_day_usd"])

    def _build_storage(self, storage: StorageSpec, data: dict) -> None:
        primary = data.get("primary", {})
        if isinstance(primary, dict):
            storage.backend = str(primary.get("backend", storage.backend))
            storage.path = str(primary.get("path", storage.path))
        archive = data.get("archive", {})
        if isinstance(archive, dict):
            storage.archive_backend = str(archive.get("backend", ""))
            storage.archive_bucket = str(archive.get("bucket", ""))
            storage.archive_after_days = int(archive.get("after_days", 30))

    def _build_router(self, router: RouterSpec, data: dict) -> None:
        if "task_router" in data:
            router.task_router = str(data["task_router"])
        if "overflow_policy" in data:
            router.overflow_policy = str(data["overflow_policy"])
        if "backpressure_threshold" in data:
            router.backpressure_threshold = int(data["backpressure_threshold"])

    def _build_federation(self, fed: FederationSpec, data: dict) -> None:
        fed.enabled = bool(data.get("enabled", False))
        fed.auto_federate_critical = bool(data.get("auto_federate_critical", True))
        fed.ingest_channels = [str(c) for c in data.get("ingest_channels", [])]
        for peer in data.get("peers", []):
            if isinstance(peer, dict) and "url" in peer:
                fed.peers.append(FederationPeerSpec(
                    url=str(peer["url"]),
                    trust_level=str(peer.get("trust_level", "restricted")),
                    push_channels=[str(c) for c in peer.get("push_channels", [])],
                    api_key_env=str(peer.get("api_key_env", "")),
                ))
