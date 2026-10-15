"""Unit tests for StorageNode: persistence and semantic indexing."""
from __future__ import annotations

import sqlite3
from contextlib import closing

from probable_intel.nexus.spec import NodeSpec
from probable_intel.nodes.archivists import storage_node
from probable_intel.nodes.archivists.storage_node import StorageNode
from probable_intel.spine.packet import IntelPacket
from probable_intel.spine.spine import Spine
from probable_intel.storage.vector_backend import search
from tests.fakes import HashingEmbedder

CHANNEL = "to.store"


def _make_node(spine: Spine, db_path, **config) -> StorageNode:
    spec = NodeSpec(
        node_type="StorageNode", node_id="archivist.test", apparatus_id="test",
        subscribe_channels=[CHANNEL], config={"path": str(db_path), **config},
    )
    return StorageNode(spec, spine)


def _article(packet_type: str = "SentimentPacket", content: str | None = "APT28 phishing wave hits ministries",
             source_hash: str = "hash-apt28") -> IntelPacket:
    payload = {"title": "APT28 phishing"}
    if content is not None:
        payload["content"] = content
    return IntelPacket(packet_type=packet_type, source_node_id="test", apparatus_id="test",
                       channel=CHANNEL, payload=payload, source_hash=source_hash)


async def _store(node: StorageNode, spine: Spine, *packets: IntelPacket) -> None:
    for packet in packets:
        await spine.publish(CHANNEL, packet)
        await node.run()


def _rows(db_path, sql: str) -> list:
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute(sql).fetchall()


async def test_packets_are_persisted(tmp_path):
    db, spine = tmp_path / "intel.db", Spine()
    node = _make_node(spine, db, vector_index=False)
    await node.setup()

    await _store(node, spine, _article(), _article("ThreatPacket"))
    await node.teardown()

    assert sorted(r[0] for r in _rows(db, "SELECT packet_type FROM intel_packets")) == [
        "SentimentPacket", "ThreatPacket",
    ]


async def test_each_article_is_embedded_once_across_pipeline_stages(tmp_path):
    db, spine, embedder = tmp_path / "intel.db", Spine(), HashingEmbedder()
    node = _make_node(spine, db)
    node._embedder = embedder
    await node.setup()

    await _store(node, spine, _article("SentimentPacket"), _article("EntityPacket"),
                 _article("ThreatPacket"), _article(content="Unrelated quarterly earnings", source_hash="hash-2"))
    await node.teardown()

    assert embedder.calls == 2  # one per distinct source_hash, not per stored packet
    assert sorted(r[0] for r in _rows(db, "SELECT source_hash FROM packet_vectors")) == ["hash-2", "hash-apt28"]
    [query] = embedder.embed(["APT28 phishing ministries"])
    assert search(db, query, model=embedder.model_name, top_k=1)[0].source_hash == "hash-apt28"


async def test_packets_without_content_are_stored_but_not_indexed(tmp_path):
    db, spine, embedder = tmp_path / "intel.db", Spine(), HashingEmbedder()
    node = _make_node(spine, db)
    node._embedder = embedder
    await node.setup()

    await _store(node, spine, _article("GraphSummaryPacket", content=None))
    await node.teardown()

    assert embedder.calls == 0
    assert len(_rows(db, "SELECT 1 FROM intel_packets")) == 1
    assert _rows(db, "SELECT 1 FROM packet_vectors") == []


async def test_vector_index_can_be_disabled(tmp_path):
    db, spine, embedder = tmp_path / "intel.db", Spine(), HashingEmbedder()
    node = _make_node(spine, db, vector_index=False)
    node._embedder = embedder
    await node.setup()

    await _store(node, spine, _article())
    await node.teardown()

    assert embedder.calls == 0
    assert _rows(db, "SELECT name FROM sqlite_master WHERE name='packet_vectors'") == []


async def test_storage_works_without_the_semantic_extra(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_node, "load_embedder", lambda model: None)
    db, spine = tmp_path / "intel.db", Spine()
    node = _make_node(spine, db)
    await node.setup()

    await _store(node, spine, _article())
    await node.teardown()

    assert node._vectors is None
    assert len(_rows(db, "SELECT 1 FROM intel_packets")) == 1


async def test_embedding_failure_never_loses_the_stored_packet(tmp_path, caplog):
    class BrokenEmbedder(HashingEmbedder):
        def embed(self, texts):
            raise RuntimeError("model crashed")

    db, spine = tmp_path / "intel.db", Spine()
    node = _make_node(spine, db)
    node._embedder = BrokenEmbedder()
    await node.setup()

    await _store(node, spine, _article())
    await node.teardown()

    assert len(_rows(db, "SELECT 1 FROM intel_packets")) == 1
    assert node._error_count == 0
    assert "semantic indexing failed" in caplog.text
