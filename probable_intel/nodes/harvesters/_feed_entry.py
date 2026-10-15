"""Field extraction for atoma RSS items and Atom entries.

atoma models the two formats differently: RSS fields are plain strings
(`description`, `link`, `guid`, `pub_date`), while Atom uses objects
(`AtomTextConstruct.value` for title/summary/content, `AtomLink.href` in
`links`, `id_`, `updated`/`published`).
"""
from __future__ import annotations

from datetime import datetime, timezone


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return _text(value[0]) if value else ""
    return str(getattr(value, "value", value) or "")


def entry_title(entry: object) -> str:
    return _text(getattr(entry, "title", None))


def entry_text(entry: object) -> str:
    """Body text: RSS description, else Atom summary/content."""
    for attr in ("description", "summary", "content", "content_encoded"):
        text = _text(getattr(entry, attr, None))
        if text:
            return text
    return ""


def entry_url(entry: object) -> str:
    link = getattr(entry, "link", None)  # RSS
    if link:
        return str(link)
    for atom_link in getattr(entry, "links", None) or []:  # Atom
        href = getattr(atom_link, "href", None)
        if href and getattr(atom_link, "rel", None) in (None, "alternate"):
            return str(href)
    return str(getattr(entry, "guid", None) or getattr(entry, "id_", None) or "")


def entry_published(entry: object) -> str:
    """ISO-8601 publish time; now (UTC) when the entry carries none."""
    for attr in ("pub_date", "published", "updated", "created"):
        value = getattr(entry, attr, None)
        if isinstance(value, datetime):
            return value.isoformat()
        if value:
            return str(value)
    return datetime.now(timezone.utc).isoformat()
