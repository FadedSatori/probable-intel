"""log.md (append-only operation log) and episodic JSONL (DESIGN.md §5.2).

log.md entries use Karpathy's grep-able prefix: `## [YYYY-MM-DD] op | title`.
"""
from __future__ import annotations

import json
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .capture import RawItem, find_raw, iter_raw
from .vault import TRUST_TIERS, Vault

OPS = frozenset({"capture", "ingest", "query", "lint", "review", "consolidate", "note", "purge"})
LOG_ENTRY = re.compile(r"^## \[(\d{4}-\d{2}-\d{2})\] (\w+) \| (.+)$")
PRIORITIES = frozenset({"high", "medium", "low"})


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _needs_separator(path: Path) -> bool:
    """True when a non-empty log does not end in a blank line, so a new entry needs one."""
    if not path.exists():
        return False
    size = path.stat().st_size
    if size == 0:
        return False
    with path.open("rb") as f:
        f.seek(max(size - 2, 0))  # only the tail is needed, not the whole log
        return f.read() != b"\n\n"


def append_log(vault: Vault, op: str, title: str, details: str = "") -> str:
    if op not in OPS:
        raise ValueError(f"op must be one of {sorted(OPS)}")
    title = " ".join(title.split())
    entry = f"## [{_utcnow():%Y-%m-%d}] {op} | {title}"
    block = entry + ("\n\n" + details.strip() if details.strip() else "") + "\n"
    separator = "\n" if _needs_separator(vault.log_md) else ""
    with vault.log_md.open("a", encoding="utf-8") as f:
        f.write(separator + block)
    return entry


def recent_log(vault: Vault, n: int = 10) -> list[tuple[str, str, str]]:
    if not vault.log_md.exists():
        return []
    entries = [
        m.groups()
        for line in vault.log_md.read_text(encoding="utf-8").splitlines()
        if (m := LOG_ENTRY.match(line))
    ]
    return entries[-n:]


def _event_id(now: datetime) -> str:
    return f"ev_{now:%Y%m%d%H%M%S}_{secrets.token_hex(3)}"


def validate_episode(
    vault: Vault, ev: dict[str, Any], raw_items: list[RawItem] | None = None
) -> list[str]:
    """Return a list of problems; empty means valid."""
    problems = []
    prop = ev.get("proposition")
    if not isinstance(prop, str) or not prop.strip():
        problems.append("proposition: required non-empty string")
    elif len(prop) > 600:
        problems.append("proposition: keep it to one event (<= 600 chars)")
    if ev.get("priority", "medium") not in PRIORITIES:
        problems.append(f"priority: one of {sorted(PRIORITIES)}")
    source = ev.get("source")
    if not isinstance(source, str) or not source:
        problems.append("source: required ('raw:<sha>' or 'session')")
    elif source.startswith("raw:") and find_raw(vault, source, raw_items) is None:
        problems.append(f"source: {source} does not resolve to a raw item")
    elif not source.startswith("raw:") and source != "session":
        problems.append("source: must be 'raw:<sha>' or 'session'")
    if "trust_tier" in ev and ev["trust_tier"] not in TRUST_TIERS:
        problems.append(f"trust_tier: one of {TRUST_TIERS}")
    if "participants" in ev and not isinstance(ev["participants"], list):
        problems.append("participants: list of names")
    anchor = ev.get("anchor")
    if anchor is not None and not (isinstance(anchor, dict) and anchor.get("exact")):
        problems.append("anchor: {exact, prefix?, suffix?} (W3C TextQuoteSelector)")
    return problems


def append_episodes(vault: Vault, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate all events first, then append atomically per batch. Raises ValueError on any problem."""
    raw_items = iter_raw(vault)
    errors = {
        i: p for i, ev in enumerate(events) if (p := validate_episode(vault, ev, raw_items))
    }
    if errors:
        detail = "; ".join(f"#{i}: {', '.join(p)}" for i, p in errors.items())
        raise ValueError(f"invalid episodes — nothing written: {detail}")

    now = _utcnow()
    tx_time = now.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    written = []
    for ev in events:
        record = {
            "id": _event_id(now),
            "tx_time": tx_time,
            "priority": ev.get("priority", "medium"),
            **{k: v for k, v in ev.items() if k not in {"id", "tx_time"}},
        }
        if record["source"].startswith("raw:") and "trust_tier" not in record:
            record["trust_tier"] = find_raw(vault, record["source"], raw_items).meta.get("trust_tier", "T3")
        written.append(record)

    path = vault.episodic / f"{now:%Y-%m}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        for record in written:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return written


def iter_episodes(vault: Vault) -> list[dict[str, Any]]:
    out = []
    for path in sorted(vault.episodic.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out
