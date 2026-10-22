"""Structural lint (DESIGN.md §4 Lint loop, §12).

Deterministic checks only. Semantic checks — contradictions, stale claims,
missing concept pages — are the anamnesis-lint skill's job, run on top of
this report.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime

import yaml

from .beliefs import band
from .capture import find_raw, iter_raw
from .logbook import iter_episodes
from .notes import Note, link_key, load_notes, wikilinks
from .vault import NOTE_TYPES, Vault

# Notes that are allowed to have no inbound links.
_ROOT_TYPES = frozenset({"moc", "journal", "loop", "prediction"})


@dataclass(frozen=True)
class Finding:
    severity: str  # "error" | "warn"
    rule: str
    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def _parse_dt(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)  # 3.11+ accepts a trailing Z
        except ValueError:
            return None
    return None


def lint(vault: Vault, *, inbox_max_age_days: int = 14, now: datetime | None = None) -> list[Finding]:
    now = now or datetime.now(UTC)
    out: list[Finding] = []

    def add(sev: str, rule: str, path: str, msg: str) -> None:
        out.append(Finding(sev, rule, path, msg))

    for required in (vault.constitution, vault.index_md, vault.log_md):
        if not required.exists():
            add("error", "missing-file", required.name, "required vault file is missing")

    notes = load_notes(vault.wiki)
    vocab = load_facets(vault)
    raw_items = iter_raw(vault)  # parse sidecars once
    by_key: dict[str, list[Note]] = defaultdict(list)
    ids: dict[str, list[Note]] = defaultdict(list)
    for n in notes:
        for k in n.keys():  # noqa: SIM118 — Note.keys() is not dict.keys()
            by_key[k].append(n)
        ids[n.id].append(n)

    for note_id, dupes in ids.items():
        if len(dupes) > 1:
            paths = ", ".join(vault.rel(d.path) for d in dupes)
            add("error", "duplicate-id", vault.rel(dupes[0].path), f"id {note_id!r} used by: {paths}")

    inbound: dict[str, int] = defaultdict(int)
    root_linked = {link_key(t) for t in wikilinks(vault.index_md.read_text(encoding="utf-8"))} \
        if vault.index_md.exists() else set()

    for n in notes:
        rel = vault.rel(n.path)
        if n.error:
            add("error", "frontmatter", rel, n.error)
            continue
        fm = n.frontmatter
        if fm is None:
            add("error", "frontmatter", rel, "no YAML frontmatter")
            continue
        for key in ("id", "type", "title"):
            if not fm.get(key):
                add("error", "frontmatter", rel, f"missing required field {key!r}")
        if fm.get("type") and fm["type"] not in NOTE_TYPES:
            add("error", "frontmatter", rel, f"unknown type {fm['type']!r}")

        for target in n.links:
            key = link_key(target)
            hits = by_key.get(key, [])
            if not hits:
                add("warn", "broken-link", rel, f"[[{target}]] does not resolve to any note")
            elif len({h.id for h in hits}) > 1:
                add("warn", "ambiguous-link", rel, f"[[{target}]] matches several notes")
            else:
                if hits[0].id != n.id:
                    inbound[hits[0].id] += 1

        _lint_sources(vault, rel, fm, add, raw_items)
        _lint_claims(rel, fm, add)
        _lint_facets(rel, fm, vocab, add)

    for n in notes:
        if n.frontmatter is None or n.error:
            continue
        rel = vault.rel(n.path)
        if n.frontmatter.get("canary"):
            continue  # the honeytoken note is deliberately unlinked
        in_index = bool(n.keys() & root_linked)
        if not in_index:
            add("warn", "not-in-index", rel, "note is not linked from index.md")
        if inbound[n.id] == 0 and not in_index and n.frontmatter.get("type") not in _ROOT_TYPES:
            add("warn", "orphan", rel, "no inbound links from other notes")

    for item in raw_items:
        rel = vault.rel(item.meta_path)
        if not item.path.exists():
            add("error", "raw-missing", rel, "sidecar has no content file")
        if item.meta.get("status", "inbox") == "inbox":
            captured = _parse_dt(item.meta.get("captured_at"))
            if captured and (now - captured).days > inbox_max_age_days:
                add("warn", "stale-inbox", vault.rel(item.path),
                    f"undistilled for {(now - captured).days} days — distill, archive, or let it expire")

    for ev in iter_episodes(vault):
        src = ev.get("source", "")
        if src.startswith("raw:") and find_raw(vault, src, raw_items) is None:
            add("error", "episode-source", ev.get("id", "?"), f"{src} does not resolve")

    order = {"error": 0, "warn": 1}
    return sorted(out, key=lambda f: (order[f.severity], f.rule, f.path))


def _lint_sources(vault: Vault, rel: str, fm: dict, add, raw_items) -> None:  # type: ignore[no-untyped-def]
    for src in fm.get("sources") or []:
        if isinstance(src, str) and src.startswith("raw:") and find_raw(vault, src, raw_items) is None:
            add("error", "source-ref", rel, f"{src} does not resolve to a raw item")


def _lint_claims(rel: str, fm: dict, add) -> None:  # type: ignore[no-untyped-def]
    claims = fm.get("claims") or []
    if claims and not fm.get("sources"):
        add("error", "unanchored-claim", rel, "note has claims but no sources (cap these at T3)")
    for c in claims:
        if not isinstance(c, dict):
            add("error", "claim", rel, "claim must be a mapping")
            continue
        cid = c.get("id", "?")
        p = c.get("p")
        if not isinstance(p, (int, float)) or not 0 <= p <= 1:
            add("error", "claim", rel, f"{cid}: p must be a number in [0, 1]")
            continue
        if c.get("band") and c["band"] != band(float(p)):
            add("error", "claim-band", rel,
                f"{cid}: band {c['band']!r} does not match p={p} (should be {band(float(p))!r})")


def load_facets(vault: Vault) -> dict[str, dict[str, str]] | None:
    """facets.yaml → {facet: {label_key: prefLabel}} covering prefLabels and altLabels.

    None when the vault has no facets.yaml (then tags are not checked).
    """
    path = vault.root / "facets.yaml"
    if not path.exists():
        return None
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    vocab: dict[str, dict[str, str]] = {}
    for facet, terms in raw.items():
        labels: dict[str, str] = {}
        for term in terms or []:
            pref = str(term.get("prefLabel", "")).strip() if isinstance(term, dict) else str(term)
            if not pref:
                continue
            labels[link_key(pref)] = pref
            alts = term.get("altLabel") or [] if isinstance(term, dict) else []
            for alt in [alts] if isinstance(alts, str) else alts:
                labels.setdefault(link_key(str(alt)), pref)
        vocab[str(facet)] = labels
    return vocab


def _lint_facets(rel: str, fm: dict, vocab, add) -> None:  # type: ignore[no-untyped-def]
    """Tags must come from facets.yaml, or folksonomy drift sets in (DESIGN.md F25)."""
    facets = fm.get("facets")
    if vocab is None or not facets:
        return
    if not isinstance(facets, dict):
        add("error", "facet", rel, "facets must be a mapping of facet → value(s)")
        return
    for facet, values in facets.items():
        if facet not in vocab:
            add("warn", "facet-unknown", rel,
                f"facet {facet!r} is not in facets.yaml ({', '.join(sorted(vocab))})")
            continue
        for value in [values] if isinstance(values, str) else values or []:
            pref = vocab[facet].get(link_key(str(value)))
            if pref is None:
                add("warn", "facet-unknown", rel,
                    f"{facet}: {value!r} is not in facets.yaml — add it there or use an existing term")
            elif pref != str(value):
                add("warn", "facet-alias", rel, f"{facet}: use prefLabel {pref!r} instead of {value!r}")
