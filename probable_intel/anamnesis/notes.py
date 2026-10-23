"""Markdown notes: YAML frontmatter and wikilinks."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
# [[target]], [[target|alias]], [[target#heading]], [[target#^block]], ![[embed]]
_WIKILINK = re.compile(r"!?\[\[([^\]\|#\n]+)(?:#[^\]\|\n]*)?(?:\|[^\]\n]*)?\]\]")
_FENCE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]*`")


class FrontmatterError(ValueError):
    pass


def split_frontmatter(text: str) -> tuple[dict[str, Any] | None, str]:
    """Return (frontmatter, body). Frontmatter is None when the note has none."""
    m = _FRONTMATTER.match(text)
    if not m:
        return None, text
    try:
        data = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as e:
        raise FrontmatterError(str(e)) from e
    if not isinstance(data, dict):
        raise FrontmatterError("frontmatter is not a mapping")
    return data, text[m.end():]


def wikilinks(body: str) -> list[str]:
    """Link targets in a note body, ignoring code blocks and inline code."""
    stripped = _INLINE_CODE.sub("", _FENCE.sub("", body))
    return [m.group(1).strip() for m in _WIKILINK.finditer(stripped)]


def link_key(target: str) -> str:
    """Normalise a link target or note name for resolution."""
    target = target.strip()
    if target.lower().endswith(".md"):
        target = target[:-3]
    target = target.rsplit("/", 1)[-1]
    return re.sub(r"[\s_]+", "-", target.lower())


@dataclass
class Note:
    path: Path
    frontmatter: dict[str, Any] | None
    body: str
    error: str | None = None
    links: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        fm_id = (self.frontmatter or {}).get("id")
        return str(fm_id) if fm_id else self.path.stem

    @property
    def title(self) -> str:
        return str((self.frontmatter or {}).get("title") or self.path.stem)

    def link_names(self) -> set[str]:
        """Every name a wikilink may use to reach this note."""
        fm = self.frontmatter or {}
        names = {self.id, self.path.stem, self.title}
        aliases = fm.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        names.update(str(a) for a in aliases)
        return {link_key(n) for n in names if n}

    @classmethod
    def load(cls, path: Path) -> Note:
        text = path.read_text(encoding="utf-8")
        try:
            fm, body = split_frontmatter(text)
            error = None
        except FrontmatterError as e:
            fm, body, error = None, text, f"invalid frontmatter: {e}"
        return cls(path=path, frontmatter=fm, body=body, error=error, links=wikilinks(body))


def load_notes(wiki_dir: Path) -> list[Note]:
    return [Note.load(p) for p in sorted(wiki_dir.rglob("*.md"))]
