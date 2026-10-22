"""Unit tests for AttackMapperNode and StixExportNode."""
from __future__ import annotations

import json

import pytest

from probable_intel.nexus.spec import NodeSpec, EmitSpec
from probable_intel.spine.spine import Spine
from probable_intel.spine.packet import IntelPacket, Priority
from probable_intel.nodes.analysts.attack_node import AttackMapperNode
from probable_intel.nodes.archivists.stix_node import StixExportNode


def _packet(payload: dict, channel: str = "raw.test", **kw) -> IntelPacket:
    return IntelPacket(
        packet_type="FeedPacket",
        source_node_id="feed.test",
        apparatus_id="test",
        channel=channel,
        payload=payload,
        **kw,
    )


# ── AttackMapperNode ──────────────────────────────────────────────────────────

def _mapper_spec(**config) -> NodeSpec:
    return NodeSpec(
        node_type="AttackMapperNode",
        node_id="analyst.attack.test",
        apparatus_id="test",
        subscribe_channels=["raw.test"],
        emit=EmitSpec(channel="analysis.attack.test", priority="normal"),
        config=config,
    )


@pytest.mark.asyncio
async def test_mapper_tags_matching_techniques():
    spine = Spine()
    node = AttackMapperNode(_mapper_spec(), spine)
    await node.setup()
    sub = spine.subscribe("analysis.attack.test")

    await spine.publish("raw.test", _packet({
        "title": "Ransomware gang behind phishing campaign",
        "content": "Operators used PowerShell to deploy the locker after lateral movement.",
    }))
    await node.run()

    out = await sub.get()
    assert out.packet_type == "AttackMappingPacket"
    ids = out.payload["attack_technique_ids"]
    assert {"T1486", "T1566", "T1059", "T1021"} <= set(ids)
    assert "T1566" in out.tags
    t1566 = next(t for t in out.payload["attack_techniques"] if t["technique_id"] == "T1566")
    assert t1566["tactics"] == ["initial-access"]
    assert t1566["matched_keywords"] == ["phishing"]
    await node.stop()


@pytest.mark.asyncio
async def test_mapper_drops_packets_without_matches():
    spine = Spine()
    node = AttackMapperNode(_mapper_spec(), spine)
    await node.setup()
    sub = spine.subscribe("analysis.attack.test")

    await spine.publish("raw.test", _packet({"title": "Quarterly earnings", "content": "Revenue grew."}))
    await node.run()

    assert sub._queue.empty()
    await node.stop()


@pytest.mark.asyncio
async def test_mapper_uses_word_boundaries():
    """'wiper' must not fire on 'swiper'; 'c2 server' must not fire inside a longer token."""
    spine = Spine()
    node = AttackMapperNode(_mapper_spec(), spine)
    await node.setup()
    assert node._scan({"content": "The swiper app shipped an update"}) == []
    assert [t["technique_id"] for t in node._scan({"content": "A new wiper targets banks"})] == ["T1485"]
    await node.stop()


@pytest.mark.asyncio
async def test_mapper_extra_techniques_extend_and_override():
    spine = Spine()
    node = AttackMapperNode(_mapper_spec(extra_techniques=[
        {"id": "T9999", "name": "Custom", "tactics": ["impact"], "keywords": ["frobnicate"]},
        {"id": "T1486", "name": "Overridden", "tactics": ["impact"], "keywords": ["lockbit"]},
        {"id": "", "keywords": ["ignored"]},
    ]), spine)
    await node.setup()

    ids = {t["technique_id"] for t in node._scan({"content": "they frobnicate; lockbit; ransomware"})}
    assert "T9999" in ids
    assert "T1486" in ids
    # override replaced the built-in keywords: 'ransomware' alone no longer matches T1486
    assert [t["technique_id"] for t in node._scan({"content": "ransomware"})] == []
    await node.stop()


@pytest.mark.asyncio
async def test_mapper_preserves_source_hash_and_priority_floor():
    spine = Spine()
    node = AttackMapperNode(_mapper_spec(), spine)
    await node.setup()
    sub = spine.subscribe("analysis.attack.test")

    src = _packet({"content": "ddos attack"}, priority=Priority.CRITICAL)
    await spine.publish("raw.test", src)
    await node.run()

    out = await sub.get()
    assert out.source_hash == src.source_hash
    assert out.priority == Priority.CRITICAL
    await node.stop()


# ── StixExportNode ────────────────────────────────────────────────────────────

def _stix_spec(export_dir: str = "", emit: bool = True) -> NodeSpec:
    return NodeSpec(
        node_type="StixExportNode",
        node_id="archive.stix.test",
        apparatus_id="test",
        subscribe_channels=["analysis.attack.test"],
        emit=EmitSpec(channel="sink.stix.test", priority="low") if emit else None,
        config={"export_dir": export_dir, "identity_name": "test-apparatus"},
    )


def _rich_packet() -> IntelPacket:
    return _packet({
        "title": "Acme hit by ransomware via CVE-2024-12345",
        "content": "Also mentions cve-2024-12345 again and CVE-2023-0001.",
        "url": "https://example.com/story",
        "entities": [
            {"text": "CVE-2024-12345", "type": "CVE"},
            {"text": "Acme Corp", "type": "ORG"},
            {"text": "Jane", "type": "PERSON"},
        ],
        "attack_techniques": [{
            "technique_id": "T1486",
            "name": "Data Encrypted for Impact",
            "tactics": ["impact"],
            "matched_keywords": ["ransomware"],
        }],
        "threat_matches": [{"severity": "HIGH", "label": "ransomware-signal"}],
    }, channel="analysis.attack.test", confidence=0.8)


def test_bundle_structure():
    node = StixExportNode(_stix_spec(), Spine())
    node._identity_name = "test-apparatus"
    bundle = node.build_bundle(_rich_packet())

    assert bundle["type"] == "bundle"
    assert bundle["id"].startswith("bundle--")
    by_type: dict[str, list] = {}
    for o in bundle["objects"]:
        by_type.setdefault(o["type"], []).append(o)

    assert {v["name"] for v in by_type["vulnerability"]} == {"CVE-2024-12345", "CVE-2023-0001"}
    (ap,) = by_type["attack-pattern"]
    assert ap["external_references"][0]["external_id"] == "T1486"
    assert ap["external_references"][0]["url"] == "https://attack.mitre.org/techniques/T1486/"
    assert ap["kill_chain_phases"] == [{"kill_chain_name": "mitre-attack", "phase_name": "impact"}]

    orgs = [i for i in by_type["identity"] if i["identity_class"] == "organization"]
    assert [o["name"] for o in orgs] == ["Acme Corp"]
    (producer,) = [i for i in by_type["identity"] if i["identity_class"] == "system"]
    assert producer["name"] == "test-apparatus"

    (report,) = by_type["report"]
    assert report["confidence"] == 80
    assert report["labels"] == ["ransomware-signal"]
    assert report["external_references"][0]["url"] == "https://example.com/story"
    ids = {o["id"] for o in bundle["objects"]}
    assert set(report["object_refs"]) <= ids
    assert producer["id"] not in report["object_refs"]
    assert len(report["object_refs"]) == 4  # 2 CVEs + 1 technique + 1 org
    assert all(o["spec_version"] == "2.1" for o in bundle["objects"])


def test_subtechnique_url_uses_slash():
    node = StixExportNode(_stix_spec(), Spine())
    node._identity_name = "x"
    p = _packet({"attack_techniques": [
        {"technique_id": "T1059.001", "name": "PowerShell", "tactics": ["execution"]}
    ]})
    bundle = node.build_bundle(p)
    ap = next(o for o in bundle["objects"] if o["type"] == "attack-pattern")
    assert ap["external_references"][0]["url"].endswith("/T1059/001/")


def test_ids_are_deterministic():
    node = StixExportNode(_stix_spec(), Spine())
    node._identity_name = "x"
    a = node.build_bundle(_rich_packet())
    b = node.build_bundle(_rich_packet())
    # different packets (different timestamps/hash inputs) but same content → same hash → same ids
    assert a["id"] == b["id"]
    assert [o["id"] for o in a["objects"]] == [o["id"] for o in b["objects"]]


def test_packet_with_nothing_to_reference_is_skipped():
    node = StixExportNode(_stix_spec(), Spine())
    node._identity_name = "x"
    assert node.build_bundle(_packet({"title": "nothing here", "entities": [{"text": "Jane", "type": "PERSON"}]})) is None


@pytest.mark.asyncio
async def test_export_writes_file_and_emits(tmp_path):
    spine = Spine()
    node = StixExportNode(_stix_spec(str(tmp_path)), spine)
    await node.setup()
    sub = spine.subscribe("sink.stix.test")

    pkt = _rich_packet()
    await spine.publish("analysis.attack.test", pkt)
    await node.run()

    path = tmp_path / f"{pkt.source_hash}.json"
    assert path.exists()
    on_disk = json.loads(path.read_text())
    assert on_disk["type"] == "bundle"

    out = await sub.get()
    assert out.packet_type == "StixBundlePacket"
    assert out.payload["export_path"] == str(path)
    assert out.payload["object_count"] == len(on_disk["objects"])
    assert out.source_hash == pkt.source_hash
    await node.stop()


@pytest.mark.asyncio
async def test_export_reexport_overwrites_not_duplicates(tmp_path):
    spine = Spine()
    node = StixExportNode(_stix_spec(str(tmp_path), emit=False), spine)
    await node.setup()

    for _ in range(2):
        await spine.publish("analysis.attack.test", _rich_packet())
        await node.run()

    assert len(list(tmp_path.glob("*.json"))) == 1
    await node.stop()
