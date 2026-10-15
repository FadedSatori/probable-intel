"""Unit tests for the semantic memory layer (storage/vector_backend.py)."""
from __future__ import annotations

import importlib.util
import sys
import types

import pytest

from probable_intel.spine.packet import IntelPacket
from probable_intel.storage import vector_backend
from probable_intel.storage.sqlite_backend import SQLiteBackend
from probable_intel.storage.vector_backend import (
    MAX_SEQ_TOKENS,
    SentenceTransformerEmbedder,
    VectorIndex,
    load_embedder,
    search,
)
from tests.fakes import HashingEmbedder

HAS_SQLITE_VEC = importlib.util.find_spec("sqlite_vec") is not None
SEARCH_PATHS = [False] + ([True] if HAS_SQLITE_VEC else [])

ARTICLES = {
    "hospital": ("Ransomware cripples hospitals", "https://x.test/1",
                 "A ransomware gang encrypted hospital patient records and demanded a ransom."),
    "vpn": ("VPN zero-day exploited", "https://x.test/2",
            "Attackers exploit a zero-day in corporate VPN appliances to steal credentials."),
    "patch": ("Quarterly storage patch", "https://x.test/3",
              "The vendor shipped a routine quarterly update for its storage product line."),
}


def _packet(packet_type: str, key: str, **extra) -> IntelPacket:
    title, url, content = ARTICLES[key]
    return IntelPacket(
        packet_type=packet_type, source_node_id="test", apparatus_id="test", channel="c",
        payload={"title": title, "url": url, "content": content, **extra},
        source_hash=f"hash-{key}",
    )


async def _build_db(path, embedder: HashingEmbedder) -> None:
    """Store every article at two pipeline stages (plus a threat for one) and index it."""
    backend = SQLiteBackend(path)
    await backend.open()
    index = VectorIndex(backend.connection, embedder.model_name)
    await index.ensure_schema()
    for key in ARTICLES:
        await backend.save(_packet("SentimentPacket", key, sentiment_score=-0.5))
        await backend.save(_packet("EntityPacket", key, entities=[]))
        [vector] = embedder.embed([ARTICLES[key][2]])
        await index.add(f"hash-{key}", vector)
    await backend.save(_packet("ThreatPacket", "hospital", max_severity="HIGH"))
    await backend.save(_packet("ThreatPacket", "hospital", max_severity="CRITICAL"))
    await backend.close()


@pytest.mark.parametrize("use_sqlite_vec", SEARCH_PATHS)
async def test_most_similar_article_ranks_first(tmp_path, use_sqlite_vec):
    db, embedder = tmp_path / "intel.db", HashingEmbedder()
    await _build_db(db, embedder)
    [query] = embedder.embed(["hospital ransomware patient records"])

    hits = search(db, query, model=embedder.model_name, top_k=3, use_sqlite_vec=use_sqlite_vec)

    assert [h.source_hash for h in hits][0] == "hash-hospital"
    assert hits[0].score > hits[1].score >= hits[2].score
    assert -1.0 <= hits[-1].score <= 1.0


async def test_both_search_paths_agree(tmp_path):
    if not HAS_SQLITE_VEC:
        pytest.skip("sqlite-vec not installed")
    db, embedder = tmp_path / "intel.db", HashingEmbedder()
    await _build_db(db, embedder)
    [query] = embedder.embed(["zero-day exploit against VPN credentials"])

    in_sql = search(db, query, model=embedder.model_name, use_sqlite_vec=True)
    in_python = search(db, query, model=embedder.model_name, use_sqlite_vec=False)

    assert [h.source_hash for h in in_sql] == [h.source_hash for h in in_python]
    assert [round(h.score, 5) for h in in_sql] == [round(h.score, 5) for h in in_python]


async def test_hit_summarises_every_stage_of_one_article(tmp_path):
    db, embedder = tmp_path / "intel.db", HashingEmbedder()
    await _build_db(db, embedder)
    [query] = embedder.embed(["hospital ransomware"])

    hit = search(db, query, model=embedder.model_name, top_k=1)[0]

    assert hit.title == "Ransomware cripples hospitals"
    assert hit.url == "https://x.test/1"
    assert hit.snippet.startswith("A ransomware gang encrypted")
    assert hit.packet_types == ["SentimentPacket", "EntityPacket", "ThreatPacket"]
    assert hit.max_severity == "CRITICAL"  # highest by rank, not alphabetical
    assert hit.first_seen


async def test_vectors_from_another_model_are_ignored(tmp_path):
    db, embedder = tmp_path / "intel.db", HashingEmbedder()
    await _build_db(db, embedder)
    [query] = embedder.embed(["hospital ransomware"])

    assert search(db, query, model="some-other-model") == []


async def test_top_k_limits_results(tmp_path):
    db, embedder = tmp_path / "intel.db", HashingEmbedder()
    await _build_db(db, embedder)
    [query] = embedder.embed(["update"])

    assert len(search(db, query, model=embedder.model_name, top_k=2)) == 2


async def test_index_add_is_idempotent_and_has_reports_it(tmp_path):
    backend = SQLiteBackend(tmp_path / "intel.db")
    await backend.open()
    index = VectorIndex(backend.connection, "m")
    await index.ensure_schema()

    assert not await index.has("h1")
    await index.add("h1", [1.0, 0.0])
    await index.add("h1", [0.0, 1.0])  # ignored: one vector per article per model

    assert await index.has("h1")
    cursor = await backend.connection.execute("SELECT COUNT(*) FROM packet_vectors")
    assert (await cursor.fetchone())[0] == 1
    await backend.close()


def test_search_on_a_database_with_no_index_returns_nothing(tmp_path):
    import sqlite3
    db = tmp_path / "plain.db"
    sqlite3.connect(db).close()
    assert search(db, [1.0, 0.0], model="m") == []


def test_load_embedder_without_the_extra_returns_none(monkeypatch):
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)  # import → ImportError
    assert load_embedder() is None


def test_load_embedder_returns_none_when_the_model_cannot_load(monkeypatch):
    def unavailable(model_name):
        raise OSError("model download blocked")
    monkeypatch.setattr(vector_backend, "SentenceTransformerEmbedder", unavailable)
    assert load_embedder() is None


def test_sentence_transformer_wrapper_truncates_and_normalizes(monkeypatch):
    calls = {}

    class FakeModel:
        def __init__(self, name):
            calls["name"] = name
            self.max_seq_length = 256

        def encode(self, texts, normalize_embeddings=False):
            calls["normalize"] = normalize_embeddings
            return types.SimpleNamespace(tolist=lambda: [[1.0, 0.0] for _ in texts])

    monkeypatch.setitem(sys.modules, "sentence_transformers",
                        types.SimpleNamespace(SentenceTransformer=FakeModel))

    embedder = SentenceTransformerEmbedder("all-MiniLM-L6-v2")

    assert embedder.embed(["a", "b"]) == [[1.0, 0.0], [1.0, 0.0]]
    assert calls == {"name": "all-MiniLM-L6-v2", "normalize": True}
    assert embedder._model.max_seq_length == MAX_SEQ_TOKENS == 512
