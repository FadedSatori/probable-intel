"""Vault layout, discovery and initialisation."""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

TEMPLATE_DIR = Path(__file__).parent / "template"

# Template entries stored without a leading dot so packaging never drops them.
_DOT_RENAMES = {"_claude": ".claude", "_gitignore": ".gitignore"}

# note type → wiki subfolder (folders are for humans; links resolve by id/title/alias)
TYPE_DIRS: dict[str, str] = {
    "concept": "concepts",
    "entity": "entities",
    "source": "sources",
    "ach": "ach",
    "loop": "loops",
    "prediction": "predictions",
    "project": "projects",
    "trail": "trails",
    "moc": "maps",
    "journal": "journal",
}
NOTE_TYPES = frozenset(TYPE_DIRS)
TRUST_TIERS = ("T0", "T1", "T2", "T3", "TQ")

_DIRS = (
    "inbox",
    "raw",
    "episodic",
    "beliefs",
    "retention",
    *(f"wiki/{d}" for d in TYPE_DIRS.values()),
)


class VaultNotFound(Exception):
    """No vault (a directory containing constitution.md) at or above the given path."""


@dataclass(frozen=True)
class Vault:
    root: Path

    # ── layout ────────────────────────────────────────────────────────────
    @property
    def inbox(self) -> Path:
        return self.root / "inbox"

    @property
    def raw(self) -> Path:
        return self.root / "raw"

    @property
    def episodic(self) -> Path:
        return self.root / "episodic"

    @property
    def wiki(self) -> Path:
        return self.root / "wiki"

    @property
    def constitution(self) -> Path:
        return self.root / "constitution.md"

    @property
    def index_md(self) -> Path:
        return self.root / "index.md"

    @property
    def log_md(self) -> Path:
        return self.root / "log.md"

    @property
    def state_dir(self) -> Path:
        """Derived, disposable state (search index). Never synced, never committed."""
        return self.root / ".anamnesis"

    @property
    def index_db(self) -> Path:
        return self.state_dir / "index.db"

    # ── discovery ─────────────────────────────────────────────────────────
    @classmethod
    def find(cls, start: Path | None = None) -> Vault:
        here = (start or Path.cwd()).resolve()
        for candidate in (here, *here.parents):
            if (candidate / "constitution.md").is_file():
                return cls(candidate)
        raise VaultNotFound(f"no constitution.md at or above {here}")

    # ── creation ──────────────────────────────────────────────────────────
    @classmethod
    def init(cls, root: Path, *, force: bool = False) -> Vault:
        """Create a vault from the bundled template.

        Existing files are never overwritten unless ``force`` is set, so
        re-running init on a live vault only fills in what is missing
        (e.g. skills added in a newer release).
        """
        root = root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        for rel in _DIRS:
            (root / rel).mkdir(parents=True, exist_ok=True)

        for src in sorted(TEMPLATE_DIR.rglob("*")):
            if src.is_dir() or src.name == "__pycache__" or src.suffix == ".pyc":
                continue
            rel_parts = [_DOT_RENAMES.get(p, p) for p in src.relative_to(TEMPLATE_DIR).parts]
            dst = root.joinpath(*rel_parts)
            if dst.exists() and not force:
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)

        for ledger in ("beliefs/ledger.jsonl", "retention/reviews.jsonl"):
            (root / ledger).touch(exist_ok=True)

        from .guard import ensure_canary, new_seal_words, render_seal_list

        constitution = root / "constitution.md"
        text = constitution.read_text(encoding="utf-8")
        if "{{SEAL_WORDS}}" in text:  # freshly copied: give this vault its own seal words
            constitution.write_text(
                text.replace("{{SEAL_WORDS}}", render_seal_list(new_seal_words())), encoding="utf-8"
            )
        vault = cls(root)
        ensure_canary(vault)
        return vault

    def rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()
