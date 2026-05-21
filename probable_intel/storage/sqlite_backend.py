from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:
    from ..spine.packet import IntelPacket

log = logging.getLogger(__name__)

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS intel_packets (
    id TEXT PRIMARY KEY,
    packet_type TEXT NOT NULL,
    source_node_id TEXT NOT NULL,
    apparatus_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    timestamp_utc TEXT NOT NULL,
    trust_level INTEGER NOT NULL,
    confidence REAL NOT NULL,
    priority INTEGER NOT NULL,
    source_hash TEXT NOT NULL,
    tags TEXT NOT NULL,
    provenance TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_channel ON intel_packets(channel);
CREATE INDEX IF NOT EXISTS idx_apparatus ON intel_packets(apparatus_id);
CREATE INDEX IF NOT EXISTS idx_hash ON intel_packets(source_hash);
CREATE INDEX IF NOT EXISTS idx_ts ON intel_packets(timestamp_utc);
"""


class SQLiteBackend:
    def __init__(self, db_path: Path | str) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        await self._db.executescript(_CREATE_SQL)
        await self._db.commit()
        log.info("sqlite backend opened: %s", self._path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    async def save(self, packet: "IntelPacket") -> None:
        if not self._db:
            raise RuntimeError("backend not open")
        try:
            await self._db.execute(
                """
                INSERT OR IGNORE INTO intel_packets
                (id, packet_type, source_node_id, apparatus_id, channel,
                 timestamp_utc, trust_level, confidence, priority,
                 source_hash, tags, provenance, payload)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    str(packet.packet_id),
                    packet.packet_type,
                    packet.source_node_id,
                    packet.apparatus_id,
                    packet.channel,
                    packet.timestamp_utc.isoformat(),
                    int(packet.trust_level),
                    packet.confidence,
                    int(packet.priority),
                    packet.source_hash,
                    json.dumps(packet.tags),
                    json.dumps(packet.provenance),
                    json.dumps(packet.payload, default=str),
                ),
            )
            await self._db.commit()
        except Exception as e:
            log.error("sqlite save error: %s", e)
