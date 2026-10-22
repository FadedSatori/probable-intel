"""Lexical search over the vault: SQLite FTS5 + BM25 (DESIGN.md §7, stage 1).

Phase 0 index. It is a pure projection of the Markdown files: delete
.anamnesis/index.db and it rebuilds on the next search. Vector search,
graph expansion and reranking arrive in Phase 1/3.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .capture import iter_raw
from .notes import FrontmatterError, split_frontmatter
from .vault import Vault

_TEXT_EXTS = frozenset({".md", ".markdown", ".txt"})
_CHUNK_CHARS = 1200
_SCHEMA = """
CREATE TABLE IF NOT EXISTS doc (
  id INTEGER PRIMARY KEY, path TEXT UNIQUE, kind TEXT, ref TEXT, title TEXT, mtime REAL
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunk USING fts5(
  context, body, doc_id UNINDEXED, tokenize='porter unicode61'
);
"""


@dataclass(frozen=True)
class Hit:
    path: str
    kind: str  # "note" | "raw"
    ref: str  # note id or raw:<sha>
    title: str
    snippet: str
    score: float


def _chunks(body: str) -> list[str]:
    """Paragraph-packed chunks of roughly _CHUNK_CHARS."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        if cur and len(cur) + len(p) > _CHUNK_CHARS:
            chunks.append(cur)
            cur = ""
        cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        chunks.append(cur)
    return chunks or [""]


def _sources(vault: Vault) -> list[tuple[Path, str, str, str, str]]:
    """(path, kind, ref, title, body) for every indexable file."""
    out = []
    for path in sorted(vault.wiki.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        try:
            fm, body = split_frontmatter(text)
        except FrontmatterError:
            fm, body = None, text
        fm = fm or {}
        out.append((path, "note", str(fm.get("id") or path.stem), str(fm.get("title") or path.stem), body))
    for item in iter_raw(vault):
        if item.path.suffix.lower() in _TEXT_EXTS and item.path.exists():
            body = item.path.read_text(encoding="utf-8", errors="replace")
            out.append((item.path, "raw", item.ref, str(item.meta.get("title") or item.path.stem), body))
    return out


def _connect(vault: Vault) -> sqlite3.Connection:
    vault.state_dir.mkdir(exist_ok=True)
    conn = sqlite3.connect(vault.index_db)
    conn.executescript(_SCHEMA)
    return conn


def build_index(vault: Vault, *, full: bool = False) -> int:
    """Incrementally (re)index changed files; returns the number of files indexed."""
    if full and vault.index_db.exists():
        vault.index_db.unlink()
    conn = _connect(vault)
    try:
        known = {p: (i, m) for i, p, m in conn.execute("SELECT id, path, mtime FROM doc")}
        seen, changed = set(), 0
        for path, kind, ref, title, body in _sources(vault):
            rel = vault.rel(path)
            mtime = path.stat().st_mtime
            seen.add(rel)
            if rel in known and known[rel][1] == mtime:
                continue
            if rel in known:
                conn.execute("DELETE FROM chunk WHERE doc_id = ?", (known[rel][0],))
                conn.execute("DELETE FROM doc WHERE id = ?", (known[rel][0],))
            cur = conn.execute(
                "INSERT INTO doc (path, kind, ref, title, mtime) VALUES (?, ?, ?, ?, ?)",
                (rel, kind, ref, title, mtime),
            )
            doc_id = cur.lastrowid
            # Cheap contextual-retrieval stand-in: prefix each chunk with what it belongs to.
            context = f"{title} ({kind} {ref})"
            conn.executemany(
                "INSERT INTO chunk (context, body, doc_id) VALUES (?, ?, ?)",
                [(context, c, doc_id) for c in _chunks(body)],
            )
            changed += 1
        for rel, (doc_id, _) in known.items():
            if rel not in seen:
                conn.execute("DELETE FROM chunk WHERE doc_id = ?", (doc_id,))
                conn.execute("DELETE FROM doc WHERE id = ?", (doc_id,))
        conn.commit()
        return changed
    finally:
        conn.close()


def _fts_query(text: str) -> str:
    """Turn free text into a safe FTS5 OR-query (no syntax errors on punctuation)."""
    terms = re.findall(r"\w+", text.lower())
    return " OR ".join(f'"{t}"' for t in dict.fromkeys(terms))


def search(vault: Vault, query: str, *, limit: int = 10, kind: str | None = None) -> list[Hit]:
    fts = _fts_query(query)
    if not fts:
        return []
    build_index(vault)
    conn = _connect(vault)
    try:
        sql = """
            SELECT d.path, d.kind, d.ref, d.title,
                   snippet(chunk, 1, '**', '**', ' … ', 24) AS snip,
                   bm25(chunk, 2.0, 1.0) AS score
            FROM chunk JOIN doc d ON d.id = chunk.doc_id
            WHERE chunk MATCH ?
        """
        params: list[object] = [fts]
        if kind:
            sql += " AND d.kind = ?"
            params.append(kind)
        sql += " ORDER BY score LIMIT ?"
        params.append(limit * 4)
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    best: dict[str, Hit] = {}  # one hit per document, its best chunk
    for path, kind_, ref, title, snip, score in rows:
        if path not in best:
            best[path] = Hit(path, kind_, ref, title, " ".join(snip.split()), -score)
    return list(best.values())[:limit]
