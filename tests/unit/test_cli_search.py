"""`pi search` CLI tests (embedder swapped for a deterministic stand-in)."""
from __future__ import annotations

from typer.testing import CliRunner

from probable_intel.cli.main import app
from probable_intel.spine.packet import IntelPacket
from probable_intel.storage import vector_backend
from probable_intel.storage.sqlite_backend import SQLiteBackend
from probable_intel.storage.vector_backend import VectorIndex
from tests.fakes import HashingEmbedder

runner = CliRunner()


async def _db_with(path, *articles: tuple[str, str, str, str]) -> None:
    """articles: (source_hash, title, url, content)."""
    embedder = HashingEmbedder()
    backend = SQLiteBackend(path)
    await backend.open()
    index = VectorIndex(backend.connection, embedder.model_name)
    await index.ensure_schema()
    for source_hash, title, url, content in articles:
        await backend.save(IntelPacket(
            packet_type="ThreatPacket", source_node_id="t", apparatus_id="t", channel="c",
            payload={"title": title, "url": url, "content": content, "max_severity": "HIGH"},
            source_hash=source_hash,
        ))
        await index.add(source_hash, embedder.embed([content])[0])
    await backend.close()


async def test_search_prints_ranked_hits(tmp_path, monkeypatch):
    db = tmp_path / "intel.db"
    await _db_with(
        db,
        ("h1", "Ransomware cripples hospitals", "https://x.test/1", "ransomware gang encrypted hospital records"),
        ("h2", "Quarterly patch", "https://x.test/2", "vendor shipped a routine storage update"),
    )
    monkeypatch.setattr(vector_backend, "load_embedder", lambda model: HashingEmbedder())

    result = runner.invoke(app, ["search", "hospital ransomware", "--db", str(db), "--top-k", "1"])

    assert result.exit_code == 0, result.output
    assert " 1. " in result.output and "[HIGH] Ransomware cripples hospitals" in result.output
    assert "https://x.test/1" in result.output
    assert "Quarterly patch" not in result.output  # --top-k 1


def test_search_reports_a_missing_database(tmp_path):
    result = runner.invoke(app, ["search", "anything", "--db", str(tmp_path / "missing.db")])
    assert result.exit_code == 1
    assert "no database" in result.output


async def test_search_explains_how_to_enable_when_the_extra_is_missing(tmp_path, monkeypatch):
    db = tmp_path / "intel.db"
    await _db_with(db)
    monkeypatch.setattr(vector_backend, "load_embedder", lambda model: None)

    result = runner.invoke(app, ["search", "anything", "--db", str(db)])

    assert result.exit_code == 1
    assert "[semantic]" in result.output
