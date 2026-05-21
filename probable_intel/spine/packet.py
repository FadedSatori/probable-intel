from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any
from uuid import UUID, uuid4


class Priority(IntEnum):
    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4


class TrustLevel(IntEnum):
    UNCLASSIFIED = 0
    RESTRICTED = 1
    CLASSIFIED = 2
    TOP_SECRET = 3


@dataclass
class IntelPacket:
    packet_type: str
    source_node_id: str
    apparatus_id: str
    channel: str
    payload: dict[str, Any]

    packet_id: UUID = field(default_factory=uuid4)
    timestamp_utc: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    trust_level: TrustLevel = TrustLevel.UNCLASSIFIED
    confidence: float = 1.0
    priority: Priority = Priority.NORMAL
    ttl_seconds: int = 86400
    provenance: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source_hash: str = field(default="")

    def __post_init__(self) -> None:
        if not self.source_hash:
            self.source_hash = self._hash_payload()
        if self.source_node_id not in self.provenance:
            self.provenance = [self.source_node_id] + list(self.provenance)

    def _hash_payload(self) -> str:
        raw = json.dumps(self.payload, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def relay(self, node_id: str, new_channel: str, **overrides: Any) -> "IntelPacket":
        """Return a new packet forwarded through node_id onto new_channel."""
        return IntelPacket(
            packet_type=overrides.get("packet_type", self.packet_type),
            source_node_id=node_id,
            apparatus_id=self.apparatus_id,
            channel=new_channel,
            payload=overrides.get("payload", dict(self.payload)),
            trust_level=overrides.get("trust_level", self.trust_level),
            confidence=overrides.get("confidence", self.confidence),
            priority=overrides.get("priority", self.priority),
            ttl_seconds=self.ttl_seconds,
            provenance=[node_id] + list(self.provenance),
            tags=overrides.get("tags", list(self.tags)),
            source_hash=self.source_hash,
        )

    def is_expired(self) -> bool:
        age = (datetime.now(timezone.utc) - self.timestamp_utc).total_seconds()
        return age > self.ttl_seconds
