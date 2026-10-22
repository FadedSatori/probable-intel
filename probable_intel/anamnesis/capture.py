"""Capture: content-addressed raw store with YAML sidecars (DESIGN.md §3.1 L0, §5.1).

Capture never calls an LLM (failure mode C1): it must keep working when every
model provider is down.
"""
from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .vault import TRUST_TIERS, Vault

CHANNELS = frozenset({"clipper", "feed", "api", "social", "voice", "chat", "email", "manual"})
STATUSES = ("inbox", "distilled", "archived", "expired")
SHA_PREFIX = 12


@dataclass(frozen=True)
class RawItem:
    sha256: str
    path: Path
    meta_path: Path
    meta: dict[str, Any]

    @property
    def ref(self) -> str:
        """Stable reference used in notes and episodes: raw:<sha-prefix>."""
        return f"raw:{self.sha256[:SHA_PREFIX]}"


def slugify(text: str, max_len: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "untitled"


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _meta_path(path: Path) -> Path:
    return path.with_name(path.name + ".meta.yaml")


def iter_raw(vault: Vault) -> list[RawItem]:
    items = []
    for meta_path in sorted(vault.raw.rglob("*.meta.yaml")):
        meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
        content = meta_path.with_name(meta_path.name[: -len(".meta.yaml")])
        items.append(RawItem(str(meta.get("sha256", "")), content, meta_path, meta))
    return items


def find_raw(vault: Vault, ref: str, items: list[RawItem] | None = None) -> RawItem | None:
    """Resolve 'raw:<prefix>' or a bare sha prefix (>= 6 hex chars) to a raw item.

    Pass ``items`` (from one ``iter_raw`` call) when resolving many refs, so the
    sidecars are parsed once instead of once per lookup.
    """
    prefix = ref.removeprefix("raw:").lower()
    if len(prefix) < 6:
        return None
    pool = iter_raw(vault) if items is None else items
    matches = [it for it in pool if it.sha256.startswith(prefix)]
    return matches[0] if len(matches) == 1 else None


def capture_bytes(
    vault: Vault,
    data: bytes,
    *,
    title: str,
    ext: str = ".md",
    uri: str | None = None,
    channel: str = "manual",
    trust_tier: str = "T3",
    published_at: str | None = None,
    facets: dict[str, list[str]] | None = None,
) -> tuple[RawItem, bool]:
    """Store content once. Returns (item, created). Re-capturing identical bytes is a no-op."""
    if trust_tier not in TRUST_TIERS:
        raise ValueError(f"trust_tier must be one of {TRUST_TIERS}")
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of {sorted(CHANNELS)}")

    sha = hashlib.sha256(data).hexdigest()
    existing = find_raw(vault, sha)
    if existing is not None:
        return existing, False

    captured_at = _now()
    ext = ext if ext.startswith(".") else f".{ext}"
    folder = vault.raw / captured_at[:4] / captured_at[5:7]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sha[:SHA_PREFIX]}-{slugify(title)}{ext}"
    path.write_bytes(data)

    meta: dict[str, Any] = {
        "sha256": sha,
        "title": title,
        "uri": uri,
        "captured_at": captured_at,
        "published_at": published_at,
        "channel": channel,
        "trust_tier": trust_tier,
        "facets": facets or {},
        "status": "inbox",
    }
    meta_path = _meta_path(path)
    meta_path.write_text(yaml.safe_dump(meta, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return RawItem(sha, path, meta_path, meta), True


def capture_file(vault: Vault, src: Path, **kwargs: Any) -> tuple[RawItem, bool]:
    kwargs.setdefault("title", src.stem)
    kwargs.setdefault("ext", src.suffix or ".bin")
    return capture_bytes(vault, src.read_bytes(), **kwargs)


def sweep_inbox(vault: Vault, **kwargs: Any) -> list[tuple[RawItem, bool]]:
    """Capture every file dropped into inbox/, then move the original to inbox/.captured/."""
    done_dir = vault.inbox / ".captured"
    results = []
    for src in sorted(p for p in vault.inbox.iterdir() if p.is_file() and not p.name.startswith(".")):
        results.append(capture_file(vault, src, **kwargs))
        done_dir.mkdir(exist_ok=True)
        shutil.move(str(src), done_dir / src.name)
    return results


def set_status(vault: Vault, ref: str, status: str) -> RawItem:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    item = find_raw(vault, ref)
    if item is None:
        raise KeyError(f"no unique raw item for {ref!r}")
    meta = dict(item.meta, status=status, status_at=_now())
    item.meta_path.write_text(
        yaml.safe_dump(meta, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    return RawItem(item.sha256, item.path, item.meta_path, meta)
