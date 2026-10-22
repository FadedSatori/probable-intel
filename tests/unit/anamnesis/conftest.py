from __future__ import annotations

from pathlib import Path

import pytest

from probable_intel.anamnesis import Vault


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    return Vault.init(tmp_path / "vault")


def write_note(vault: Vault, rel: str, frontmatter: str, body: str = "") -> Path:
    path = vault.wiki / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter.strip()}\n---\n\n{body}\n", encoding="utf-8")
    return path


def link_from_index(vault: Vault, *ids: str) -> None:
    with vault.index_md.open("a", encoding="utf-8") as f:
        for i in ids:
            f.write(f"- [[{i}]]: test\n")
