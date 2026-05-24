from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EmitSpec:
    channel: str
    priority: str = "normal"


@dataclass
class ScheduleSpec:
    interval_seconds: int | None = None
    cron: str | None = None
    jitter_seconds: int = 0


@dataclass
class RuleSpec:
    condition: str
    severity: str
    label: str


@dataclass
class HoneypotSpec:
    type: str
    path: str
    canary_id: str
    response: str = "synthetic_json"


@dataclass
class NodeSpec:
    node_type: str
    node_id: str
    apparatus_id: str
    config: dict[str, Any] = field(default_factory=dict)

    # Parsed convenience fields (populated by parser from config)
    targets: list[dict[str, Any]] = field(default_factory=list)
    subscribe_channels: list[str] = field(default_factory=list)
    emit: EmitSpec | None = None
    schedule: ScheduleSpec | None = None
    filters: dict[str, Any] = field(default_factory=dict)
    rules: list[RuleSpec] = field(default_factory=list)
    honeypots: list[HoneypotSpec] = field(default_factory=list)
    identity_profile: str = "rotate"
    proxy_pool: str = ""
    rate_limit_rpm: int = 60
    backend: dict[str, Any] = field(default_factory=dict)
    llm: "LLMSpec | None" = None

    @property
    def emits_channel(self) -> str | None:
        return self.emit.channel if self.emit else None

    @property
    def full_id(self) -> str:
        return f"{self.apparatus_id}/{self.node_id}"


@dataclass
class LLMSpec:
    provider: str = "anthropic"
    model: str = "claude-opus-4-5"
    api_key_env: str = "ANTHROPIC_API_KEY"
    max_tokens: int = 8000
    budget_per_day_usd: float = 5.0


@dataclass
class StorageSpec:
    backend: str = "sqlite"
    path: str = "/data/probable-intel/main.db"
    archive_backend: str = ""
    archive_bucket: str = ""
    archive_after_days: int = 30


@dataclass
class RouterSpec:
    task_router: str = ""
    overflow_policy: str = "drop_low_priority"
    backpressure_threshold: int = 1000


@dataclass
class FederationPeerSpec:
    url: str
    trust_level: str = "restricted"
    push_channels: list[str] = field(default_factory=list)
    api_key_env: str = ""


@dataclass
class FederationSpec:
    enabled: bool = False
    peers: list[FederationPeerSpec] = field(default_factory=list)
    auto_federate_critical: bool = True
    ingest_channels: list[str] = field(default_factory=list)


@dataclass
class ApparatusSpec:
    name: str
    version: float = 1.0
    description: str = ""
    trust_level: str = "unclassified"
    owner: str = ""
    nodes: list[NodeSpec] = field(default_factory=list)
    llm: LLMSpec = field(default_factory=LLMSpec)
    storage: StorageSpec = field(default_factory=StorageSpec)
    router: RouterSpec = field(default_factory=RouterSpec)
    federation: FederationSpec = field(default_factory=FederationSpec)

    def node_by_id(self, node_id: str) -> NodeSpec | None:
        return next((n for n in self.nodes if n.node_id == node_id), None)

    def emitting_channels(self) -> set[str]:
        return {n.emit.channel for n in self.nodes if n.emit}

    def subscribed_channels(self) -> set[str]:
        channels: set[str] = set()
        for n in self.nodes:
            channels.update(n.subscribe_channels)
        return channels
