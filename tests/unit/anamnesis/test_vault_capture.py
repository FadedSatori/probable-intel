from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from probable_intel.anamnesis import Vault, VaultNotFound
from probable_intel.anamnesis.capture import (
    capture_bytes,
    capture_file,
    find_raw,
    iter_raw,
    set_status,
    sweep_inbox,
)

SKILLS = ("anamnesis-capture", "anamnesis-ingest", "anamnesis-query", "anamnesis-lint")


def test_init_creates_layout_skills_and_dotfiles(vault: Vault):
    for rel in ("constitution.md", "CLAUDE.md", "index.md", "log.md", "facets.yaml", ".gitignore",
                "beliefs/ledger.jsonl", "retention/reviews.jsonl"):
        assert (vault.root / rel).is_file(), rel
    for d in ("inbox", "raw", "episodic", "wiki/concepts", "wiki/entities", "wiki/maps"):
        assert (vault.root / d).is_dir(), d
    for name in SKILLS:
        skill = vault.root / ".claude" / "skills" / name / "SKILL.md"
        text = skill.read_text(encoding="utf-8")
        fm = yaml.safe_load(text.split("---")[1])
        assert fm["name"] == name and len(fm["description"]) > 100


def test_init_never_overwrites_user_edits_unless_forced(vault: Vault):
    vault.constitution.write_text("# mine\n", encoding="utf-8")
    Vault.init(vault.root)
    assert vault.constitution.read_text(encoding="utf-8") == "# mine\n"
    Vault.init(vault.root, force=True)
    assert vault.constitution.read_text(encoding="utf-8") != "# mine\n"


def test_find_walks_up_and_fails_cleanly(vault: Vault, tmp_path: Path):
    assert Vault.find(vault.wiki / "concepts").root == vault.root
    with pytest.raises(VaultNotFound):
        Vault.find(tmp_path)


def test_capture_is_content_addressed_and_idempotent(vault: Vault):
    item, created = capture_bytes(vault, b"hello world", title="Greeting!", trust_tier="T2")
    assert created
    assert item.path.name.startswith(item.sha256[:12]) and item.path.name.endswith("-greeting.md")
    assert item.meta["status"] == "inbox" and item.meta["trust_tier"] == "T2"
    again, created2 = capture_bytes(vault, b"hello world", title="other title")
    assert not created2 and again.sha256 == item.sha256
    assert len(iter_raw(vault)) == 1


def test_capture_validates_tier_and_channel(vault: Vault):
    with pytest.raises(ValueError):
        capture_bytes(vault, b"x", title="x", trust_tier="T9")
    with pytest.raises(ValueError):
        capture_bytes(vault, b"x", title="x", channel="carrier-pigeon")


def test_find_raw_requires_unambiguous_prefix(vault: Vault):
    item, _ = capture_bytes(vault, b"abc", title="a")
    assert find_raw(vault, item.ref) == item
    assert find_raw(vault, "raw:ab") is None  # too short to be safe


def test_capture_file_keeps_extension(vault: Vault, tmp_path: Path):
    src = tmp_path / "paper.pdf"
    src.write_bytes(b"%PDF-1.7 fake")
    item, _ = capture_file(vault, src, trust_tier="T1")
    assert item.path.suffix == ".pdf" and item.meta["title"] == "paper"


def test_sweep_inbox_moves_originals(vault: Vault):
    (vault.inbox / "one.md").write_text("one", encoding="utf-8")
    (vault.inbox / "two.txt").write_text("two", encoding="utf-8")
    results = sweep_inbox(vault)
    assert [created for _, created in results] == [True, True]
    assert not any(p.is_file() for p in vault.inbox.iterdir())
    assert sorted(p.name for p in (vault.inbox / ".captured").iterdir()) == ["one.md", "two.txt"]


def test_set_status_updates_sidecar(vault: Vault):
    item, _ = capture_bytes(vault, b"abc", title="a")
    updated = set_status(vault, item.ref, "distilled")
    assert yaml.safe_load(updated.meta_path.read_text(encoding="utf-8"))["status"] == "distilled"
    with pytest.raises(ValueError):
        set_status(vault, item.ref, "burned")
    with pytest.raises(KeyError):
        set_status(vault, "raw:ffffffffffff", "archived")
