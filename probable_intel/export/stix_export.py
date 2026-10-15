"""STIX 2.1 export of stored threats (`pi export --format stix`).

One bundle per apparatus: every stored article whose highest ThreatPacket
severity is HIGH or CRITICAL becomes a `report` referencing `attack-pattern`
objects (from ThreatAssessNode's ATT&CK tags) and `vulnerability` objects (CVE
ids in its text). Object ids are deterministic (UUIDv5), so re-exporting the
same intel yields the same ids and SOC platforms deduplicate instead of piling
up copies.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..nodes.analysts.threat_node import _SEVERITY_RANK

_NAMESPACE = uuid.UUID("6f9d1c52-3a0e-5b7e-9c41-2d8a7e5f0b13")
_EXPORT_SEVERITIES = {"HIGH", "CRITICAL"}
_CVE = re.compile(r"\bCVE-\d{4}-\d{4,}\b", re.IGNORECASE)
# Reference objects (identity, ATT&CK techniques, CVEs) don't change between
# exports, so they get a fixed timestamp to keep (id, modified) stable.
_REFERENCE_TS = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _stix_id(object_type: str, key: str) -> str:
    return f"{object_type}--{uuid.uuid5(_NAMESPACE, f'{object_type}:{key}')}"


def _parse_ts(stored: str) -> datetime:
    # stix2 only accepts "...Z" strings; stored timestamps carry "+00:00".
    ts = datetime.fromisoformat(stored)
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _attack_url(technique_id: str) -> str:
    return "https://attack.mitre.org/techniques/" + technique_id.replace(".", "/") + "/"


@dataclass
class _Article:
    source_hash: str
    first_seen: str
    last_seen: str
    payload: dict[str, Any]
    severity: str
    rule_labels: set[str] = field(default_factory=set)
    techniques: dict[str, dict] = field(default_factory=dict)


def _load_articles(db_path: Path | str, apparatus_id: str, since: str | None) -> list[_Article]:
    """Group the apparatus's ThreatPackets by article (source_hash)."""
    sql = ("SELECT source_hash, timestamp_utc, payload FROM intel_packets "
           "WHERE packet_type = 'ThreatPacket' AND apparatus_id = ?")
    params: list[Any] = [apparatus_id]
    if since:
        sql += " AND timestamp_utc >= ?"
        params.append(since)
    articles: dict[str, _Article] = {}
    with closing(sqlite3.connect(db_path)) as conn:
        for source_hash, ts, payload_json in conn.execute(sql + " ORDER BY timestamp_utc", params):
            payload = json.loads(payload_json)
            severity = str(payload.get("max_severity", "LOW"))
            article = articles.get(source_hash)
            if article is None:
                article = articles[source_hash] = _Article(source_hash, ts, ts, payload, severity)
            elif _SEVERITY_RANK.get(severity, 0) > _SEVERITY_RANK.get(article.severity, 0):
                article.payload, article.severity = payload, severity
            article.last_seen = ts
            article.rule_labels.update(m.get("label", "") for m in payload.get("threat_matches", []))
            for technique in payload.get("attack_techniques", []):
                article.techniques[technique["id"]] = technique
    return [a for a in articles.values() if a.severity in _EXPORT_SEVERITIES]


def export_bundle(db_path: Path | str, apparatus_id: str, since: str | None = None):
    """Build the STIX 2.1 bundle (a stix2.Bundle) for one apparatus's stored threats."""
    import stix2

    if since:
        parsed = datetime.fromisoformat(since)
        since = (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).isoformat()

    identity = stix2.Identity(
        id=_stix_id("identity", "probable-intel"), name="probable-intel",
        identity_class="system", created=_REFERENCE_TS, modified=_REFERENCE_TS,
    )
    patterns: dict[str, Any] = {}
    vulnerabilities: dict[str, Any] = {}
    reports = []
    for article in _load_articles(db_path, apparatus_id, since):
        refs: list[str] = []
        for tid, technique in sorted(article.techniques.items()):
            if tid not in patterns:
                patterns[tid] = stix2.AttackPattern(
                    id=_stix_id("attack-pattern", tid), name=technique["name"],
                    external_references=[{"source_name": "mitre-attack", "external_id": tid,
                                          "url": _attack_url(tid)}],
                    kill_chain_phases=[{"kill_chain_name": "mitre-attack", "phase_name": tactic}
                                       for tactic in technique.get("tactics", [])],
                    created_by_ref=identity.id, created=_REFERENCE_TS, modified=_REFERENCE_TS,
                )
            refs.append(patterns[tid].id)
        text = f"{article.payload.get('title', '')} {article.payload.get('content', '')}"
        for cve in sorted({m.upper() for m in _CVE.findall(text)}):
            if cve not in vulnerabilities:
                vulnerabilities[cve] = stix2.Vulnerability(
                    id=_stix_id("vulnerability", cve), name=cve,
                    external_references=[{"source_name": "cve", "external_id": cve}],
                    created_by_ref=identity.id, created=_REFERENCE_TS, modified=_REFERENCE_TS,
                )
            refs.append(vulnerabilities[cve].id)

        url = article.payload.get("url")
        reports.append(stix2.Report(
            id=_stix_id("report", article.source_hash),
            name=article.payload.get("title") or "(untitled)",
            description=" ".join(str(article.payload.get("content") or "").split())[:1000] or None,
            published=_parse_ts(article.first_seen),
            created=_parse_ts(article.first_seen),
            modified=_parse_ts(article.last_seen),
            report_types=["threat-report"],
            labels=[f"severity-{article.severity.lower()}", *sorted(filter(None, article.rule_labels))],
            object_refs=refs or [identity.id],  # STIX requires at least one reference
            external_references=[{"source_name": "source", "url": url}] if url else None,
            created_by_ref=identity.id,
        ))

    return stix2.Bundle(identity, *patterns.values(), *vulnerabilities.values(), *reports)
