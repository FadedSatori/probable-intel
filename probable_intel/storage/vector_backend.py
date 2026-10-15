"""Semantic memory: embeddings of stored intel and similarity search over them.

StorageNode embeds each packet's ``payload.content`` once per ``source_hash``
(the same article relayed through several pipeline stages shares one vector)
into the ``packet_vectors`` table of its SQLite file. ``pi search`` embeds a
query with the same model and ranks stored vectors by cosine similarity —
inside SQLite via the sqlite-vec extension when it loads, else in Python.
"""
from __future__ import annotations

import heapq
import json
import logging
import math
import sqlite3
import struct
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from ..nodes.analysts.threat_node import _SEVERITY_RANK

if TYPE_CHECKING:
    import aiosqlite

log = logging.getLogger(__name__)

DEFAULT_MODEL = "all-MiniLM-L6-v2"
MAX_SEQ_TOKENS = 512

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS packet_vectors (
    source_hash TEXT NOT NULL,
    model TEXT NOT NULL,
    dim INTEGER NOT NULL,
    embedding BLOB NOT NULL,
    indexed_utc TEXT NOT NULL,
    PRIMARY KEY (source_hash, model)
);
"""


class Embedder(Protocol):
    model_name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class SentenceTransformerEmbedder:
    """Local embeddings via sentence-transformers (the optional [semantic] extra)."""

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        from sentence_transformers import SentenceTransformer

        self.model_name = model_name
        self._model = SentenceTransformer(model_name)
        self._model.max_seq_length = MAX_SEQ_TOKENS  # longer content is truncated

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._model.encode(texts, normalize_embeddings=True).tolist()


def load_embedder(model_name: str = DEFAULT_MODEL) -> Embedder | None:
    """The embedding model, or None (logged) when it can't be loaded."""
    try:
        return SentenceTransformerEmbedder(model_name)
    except ImportError:
        log.info("semantic memory unavailable: install the [semantic] extra (sentence-transformers)")
    except Exception as e:  # e.g. model download blocked
        log.warning("semantic memory unavailable: could not load model %r: %s", model_name, e)
    return None


def _pack(vector: list[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)  # float32 LE — sqlite-vec's format


def _unpack(blob: bytes, dim: int) -> tuple[float, ...]:
    return struct.unpack(f"<{dim}f", blob)


class VectorIndex:
    """Write side, on StorageNode's aiosqlite connection."""

    def __init__(self, db: "aiosqlite.Connection", model_name: str) -> None:
        self._db = db
        self._model = model_name

    async def ensure_schema(self) -> None:
        await self._db.executescript(_CREATE_SQL)
        await self._db.commit()

    async def has(self, source_hash: str) -> bool:
        cursor = await self._db.execute(
            "SELECT 1 FROM packet_vectors WHERE source_hash = ? AND model = ?",
            (source_hash, self._model),
        )
        return await cursor.fetchone() is not None

    async def add(self, source_hash: str, vector: list[float]) -> None:
        await self._db.execute(
            "INSERT OR IGNORE INTO packet_vectors "
            "(source_hash, model, dim, embedding, indexed_utc) VALUES (?,?,?,?,?)",
            (source_hash, self._model, len(vector), _pack(vector),
             datetime.now(timezone.utc).isoformat()),
        )
        await self._db.commit()


@dataclass
class SearchHit:
    source_hash: str
    score: float
    title: str = ""
    url: str = ""
    snippet: str = ""
    first_seen: str = ""
    packet_types: list[str] = field(default_factory=list)
    max_severity: str | None = None


def _load_sqlite_vec(conn: sqlite3.Connection) -> bool:
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True
    except Exception:  # not installed, or this sqlite3 build can't load extensions
        return False


def _cosine(a: list[float] | tuple[float, ...], b: tuple[float, ...]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def search(
    db_path: Path | str,
    query_vector: list[float],
    model: str,
    top_k: int = 10,
    use_sqlite_vec: bool = True,
) -> list[SearchHit]:
    """Top-k stored intel most similar to query_vector (embedded with `model`)."""
    with closing(sqlite3.connect(db_path)) as conn:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='packet_vectors'"
        ).fetchone():
            return []
        dim = len(query_vector)
        if use_sqlite_vec and _load_sqlite_vec(conn):
            ranked = conn.execute(
                "SELECT source_hash, 1 - vec_distance_cosine(embedding, ?) AS score "
                "FROM packet_vectors WHERE model = ? AND dim = ? ORDER BY score DESC LIMIT ?",
                (_pack(query_vector), model, dim, top_k),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT source_hash, embedding FROM packet_vectors WHERE model = ? AND dim = ?",
                (model, dim),
            )
            ranked = heapq.nlargest(
                top_k,
                ((h, _cosine(query_vector, _unpack(blob, dim))) for h, blob in rows),
                key=lambda r: r[1],
            )
        return [_describe(conn, h, float(score)) for h, score in ranked]


def _describe(conn: sqlite3.Connection, source_hash: str, score: float) -> SearchHit:
    """Summarise every stored packet sharing source_hash (one article, many stages)."""
    hit = SearchHit(source_hash=source_hash, score=score)
    rows = conn.execute(
        "SELECT packet_type, timestamp_utc, payload FROM intel_packets "
        "WHERE source_hash = ? ORDER BY timestamp_utc",
        (source_hash,),
    ).fetchall()
    for packet_type, ts, payload_json in rows:
        payload = json.loads(payload_json)
        hit.first_seen = hit.first_seen or ts
        if packet_type not in hit.packet_types:
            hit.packet_types.append(packet_type)
        hit.title = hit.title or str(payload.get("title") or "")
        hit.url = hit.url or str(payload.get("url") or "")
        hit.snippet = hit.snippet or " ".join(str(payload.get("content") or "").split())[:200]
        severity = payload.get("max_severity")
        if severity in _SEVERITY_RANK and (
            hit.max_severity is None or _SEVERITY_RANK[severity] > _SEVERITY_RANK[hit.max_severity]
        ):
            hit.max_severity = severity
    return hit
