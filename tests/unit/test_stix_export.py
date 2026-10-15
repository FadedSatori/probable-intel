"""STIX 2.1 export tests (`pi export --format stix`)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from typer.testing import CliRunner

from probable_intel.cli.main import app
from probable_intel.export.stix_export import export_bundle
from probable_intel.spine.packet import IntelPacket
from probable_intel.storage.sqlite_backend import SQLiteBackend

RANSOMWARE = [{"id": "T1486", "name": "Data Encrypted for Impact", "tactics": ["impact"]}]
SPRAYING = [{"id": "T1110.003", "name": "Password Spraying", "tactics": ["credential-access"]}]


def _threat(source_hash: str, severity: str, *, apparatus: str = "mission", minute: int = 0,
            title: str = "", content: str = "", techniques=(), labels=("negative-signal",)) -> IntelPacket:
    return IntelPacket(
        packet_type="ThreatPacket", source_node_id="analyst.threat", apparatus_id=apparatus,
        channel="threat.x",
        payload={"title": title, "url": f"https://news.test/{source_hash}", "content": content,
                 "max_severity": severity, "attack_techniques": list(techniques),
                 "threat_matches": [{"severity": severity, "label": lbl} for lbl in labels]},
        source_hash=source_hash,
        timestamp_utc=datetime(2026, 10, 1, 12, minute, tzinfo=timezone.utc),
    )


async def _db(path, *packets: IntelPacket):
    backend = SQLiteBackend(path)
    await backend.open()
    for p in packets:
        await backend.save(p)
    await backend.close()
    return path


async def _standard_db(tmp_path):
    return await _db(
        tmp_path / "intel.db",
        _threat("a1", "HIGH", minute=1, title="Hospital ransomware",
                content="Gang exploited CVE-2024-3400 before deploying ransomware.", techniques=RANSOMWARE),
        _threat("a1", "CRITICAL", minute=5, title="Hospital ransomware",
                content="Gang exploited CVE-2024-3400 before deploying ransomware.", techniques=RANSOMWARE,
                labels=("critical-indicator",)),
        _threat("a2", "HIGH", minute=2, title="Spraying wave", content="Password spraying hits VPNs.",
                techniques=SPRAYING),
        _threat("a3", "MEDIUM", minute=3, title="Minor", content="low-grade noise"),
        _threat("x1", "CRITICAL", apparatus="other-mission", title="Not ours"),
    )


def _objects(bundle, stix_type: str) -> list:
    return [o for o in bundle.objects if o.type == stix_type]


async def test_one_report_per_high_or_critical_article(tmp_path):
    bundle = export_bundle(await _standard_db(tmp_path), "mission")

    reports = {r.name: r for r in _objects(bundle, "report")}
    assert set(reports) == {"Hospital ransomware", "Spraying wave"}  # MEDIUM + other apparatus excluded
    hospital = reports["Hospital ransomware"]
    assert "severity-critical" in hospital.labels  # highest severity seen for the article
    assert {"critical-indicator", "negative-signal"} <= set(hospital.labels)
    assert hospital.report_types == ["threat-report"]
    assert hospital.published.minute == 1 and hospital.modified.minute == 5  # first / last seen
    assert hospital.external_references[0].url == "https://news.test/a1"


async def test_reports_reference_attack_patterns_and_cves(tmp_path):
    bundle = export_bundle(await _standard_db(tmp_path), "mission")
    by_id = {o.id: o for o in bundle.objects}

    hospital = next(r for r in _objects(bundle, "report") if r.name == "Hospital ransomware")
    referenced = {by_id[ref].type: by_id[ref] for ref in hospital.object_refs}
    pattern, vuln = referenced["attack-pattern"], referenced["vulnerability"]

    assert pattern.name == "Data Encrypted for Impact"
    assert pattern.external_references[0].external_id == "T1486"
    assert pattern.kill_chain_phases[0].phase_name == "impact"
    assert vuln.name == "CVE-2024-3400"
    spraying = next(p for p in _objects(bundle, "attack-pattern") if p.name == "Password Spraying")
    assert spraying.external_references[0].url == "https://attack.mitre.org/techniques/T1110/003/"


async def test_reexport_produces_identical_object_ids(tmp_path):
    db = await _standard_db(tmp_path)
    first = {o.id for o in export_bundle(db, "mission").objects}
    second = {o.id for o in export_bundle(db, "mission").objects}
    assert first == second


async def test_since_limits_the_export_window(tmp_path):
    bundle = export_bundle(await _standard_db(tmp_path), "mission", since="2026-10-01T12:02:00")
    assert {r.name for r in _objects(bundle, "report")} == {"Hospital ransomware", "Spraying wave"}
    later = export_bundle(await _standard_db(tmp_path / "b"), "mission", since="2026-10-01T12:04:00+00:00")
    assert {r.name for r in _objects(later, "report")} == {"Hospital ransomware"}  # only its 12:05 packet


async def test_bundle_passes_the_official_stix_validator(tmp_path):
    validator = pytest.importorskip("stix2validator")
    bundle = export_bundle(await _standard_db(tmp_path), "mission")

    results = validator.validate_string(
        bundle.serialize(), validator.ValidationOptions(version="2.1", enforce_refs=True)
    )

    assert results.is_valid, [str(e) for e in results.errors]


async def test_every_reference_resolves_within_the_bundle(tmp_path):
    """stix2-validator's enforce_refs misses dangling object_refs, so check directly."""
    data = json.loads(export_bundle(await _standard_db(tmp_path), "mission").serialize())
    ids = {o["id"] for o in data["objects"]}

    refs = []
    for obj in data["objects"]:
        for key, value in obj.items():
            if key.endswith("_ref"):
                refs.append(value)
            elif key.endswith("_refs"):
                refs.extend(value)

    assert refs and set(refs) <= ids


async def test_cli_writes_a_bundle_for_the_apparatus(tmp_path):
    db = await _standard_db(tmp_path)
    nx = tmp_path / "mission.nx"
    nx.write_text(
        'apparatus_name: "mission"\nnodes:\n  - type: StorageNode\n    id: "archivist.x"\n'
        f'    subscribe:\n      channels: ["threat.x"]\nstorage:\n  primary:\n    path: "{db}"\n'
    )
    out = tmp_path / "bundle.json"

    result = CliRunner().invoke(app, ["export", str(nx), "--format", "stix", "--output", str(out)])

    assert result.exit_code == 0, result.output
    data = json.loads(out.read_text())
    assert data["type"] == "bundle"
    assert sum(o["type"] == "report" for o in data["objects"]) == 2
    assert "exported 2 threat report(s), 2 ATT&CK technique(s), 1 CVE(s)" in result.output


def test_cli_rejects_unknown_formats(tmp_path):
    nx = tmp_path / "m.nx"
    nx.write_text('apparatus_name: "m"\nnodes: []\n')
    result = CliRunner().invoke(app, ["export", str(nx), "--format", "csv"])
    assert result.exit_code == 1 and "unsupported format" in result.output
