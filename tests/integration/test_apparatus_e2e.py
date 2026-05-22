"""End-to-end integration test: FeedNode → SentimentNode → ThreatAssessNode → StorageNode."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from probable_intel.hub.hub import Hub
from probable_intel.nexus.loader import NexusLoader
from probable_intel.spine.packet import IntelPacket, Priority

# ── Minimal RSS feed fixture ────────────────────────────────────────────────

MOCK_RSS = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Security Feed</title>
    <link>http://localhost/</link>
    <description>Test feed</description>
    <item>
      <title>Critical zero-day vulnerability discovered in widespread software</title>
      <link>http://localhost/vuln-001</link>
      <description>Attackers are actively exploiting a critical zero-day vulnerability.
      Ransomware gangs have been observed deploying this exploit in the wild.
      Security teams should patch immediately to avoid breach and catastrophic data loss.
      This is a severe threat requiring immediate remediation across all systems.</description>
      <pubDate>Wed, 21 May 2026 12:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Minor configuration change in open-source library</title>
      <link>http://localhost/update-002</link>
      <description>A minor update was released for a popular library. The change
      improves performance slightly. No security implications are expected.</description>
      <pubDate>Wed, 21 May 2026 11:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>
"""


@pytest.fixture
def mock_rss_server(httpserver):
    """Serve a mock RSS feed via pytest-localserver (or httpserver fixture)."""
    httpserver.expect_request("/feed.rss").respond_with_data(
        MOCK_RSS, content_type="application/rss+xml"
    )
    return httpserver.url_for("/feed.rss")


@pytest.fixture
def tmp_db(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def e2e_apparatus_nx(tmp_path, tmp_db):
    """Write a minimal apparatus .nx file pointing at the mock feed."""
    nx_content = f"""
apparatus_name: "e2e-test"
version: 1.0
trust_level: unclassified

nodes:
  - type: FeedNode
    id: "feed.mock"
    targets:
      - feed: "__FEED_URL__"
    schedule:
      interval: 1
    filters:
      keywords: ["vulnerability", "exploit", "ransomware", "zero-day", "breach"]
      min_word_count: 5
    emit:
      channel: "raw.feed.test"
      priority: high

  - type: SentimentNode
    id: "analyst.sentiment"
    subscribe:
      channels: ["raw.feed.test"]
    backend:
      primary: "vader"
    emit:
      channel: "analysis.sentiment.test"
      priority: normal

  - type: ThreatAssessNode
    id: "analyst.threat"
    subscribe:
      channels: ["analysis.sentiment.test"]
    rules:
      - condition: "sentiment_score < -0.3"
        severity: HIGH
        label: "negative-security-signal"
      - condition: "sentiment_score < -0.6"
        severity: CRITICAL
        label: "critical-threat-indicator"
    emit:
      channel: "threat.test"
      priority: high

  - type: StorageNode
    id: "archivist.test"
    subscribe:
      channels: ["threat.test", "analysis.sentiment.test"]
    emit:
      channel: "sink.storage"
      priority: low

storage:
  primary:
    backend: "sqlite"
    path: "{tmp_db}"
"""
    path = tmp_path / "e2e-test.nx"
    path.write_text(nx_content)
    return path, tmp_db


# ── Tests ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_spine_pipeline_delivers_packets():
    """Verify that packets flow correctly through the Spine between nodes."""
    from probable_intel.spine.spine import Spine
    from probable_intel.spine.packet import IntelPacket, Priority, TrustLevel

    spine = Spine()
    received = []

    sub = spine.subscribe("test.channel")

    packet = IntelPacket(
        packet_type="TestPacket",
        source_node_id="source",
        apparatus_id="test",
        channel="test.channel",
        payload={"content": "critical zero-day vulnerability exploit ransomware breach"},
        priority=Priority.HIGH,
    )
    await spine.publish("test.channel", packet)

    got = await asyncio.wait_for(sub.get(), timeout=1.0)
    assert got.packet_id == packet.packet_id
    assert got.priority == Priority.HIGH
    sub.close()


@pytest.mark.asyncio
async def test_sentiment_node_processes_packet():
    """SentimentNode correctly scores a threatening security text."""
    from probable_intel.spine.spine import Spine
    from probable_intel.spine.packet import IntelPacket, Priority
    from probable_intel.nexus.spec import NodeSpec, EmitSpec
    from probable_intel.nodes.analysts.sentiment_node import SentimentNode

    spine = Spine()

    spec = NodeSpec(
        node_type="SentimentNode",
        node_id="sentiment.test",
        apparatus_id="test",
        subscribe_channels=["raw.test"],
        emit=EmitSpec(channel="sentiment.out", priority="normal"),
        backend={"primary": "vader"},
    )
    node = SentimentNode(spec, spine)
    await node.setup()

    output_sub = spine.subscribe("sentiment.out")

    # Publish a threatening packet
    threat_packet = IntelPacket(
        packet_type="RawFeedPacket",
        source_node_id="feed.test",
        apparatus_id="test",
        channel="raw.test",
        payload={
            "content": (
                "CRITICAL: Attackers exploiting zero-day. Ransomware spreading. "
                "Catastrophic breach imminent. Patch immediately or face total loss."
            ),
            "title": "Critical Threat Alert",
            "url": "http://example.com/threat",
        },
        priority=Priority.HIGH,
    )
    await spine.publish("raw.test", threat_packet)

    # Run one cycle
    sub = spine.subscribe("raw.test")
    # Re-publish so the node's subscription sees it
    await spine.publish("raw.test", threat_packet)
    await node.run()

    result = await asyncio.wait_for(output_sub.get(), timeout=2.0)
    assert result.packet_type == "SentimentPacket"
    assert "sentiment_score" in result.payload
    # A threatening text should score negative
    assert result.payload["sentiment_score"] < 0

    output_sub.close()
    sub.close()
    await node.stop()


@pytest.mark.asyncio
async def test_threat_rule_eval_fires_on_negative_sentiment():
    """ThreatAssessNode fires a HIGH rule when sentiment_score < -0.3."""
    from probable_intel.spine.spine import Spine
    from probable_intel.spine.packet import IntelPacket, Priority
    from probable_intel.nexus.spec import NodeSpec, EmitSpec, RuleSpec
    from probable_intel.nodes.analysts.threat_node import ThreatAssessNode

    spine = Spine()

    spec = NodeSpec(
        node_type="ThreatAssessNode",
        node_id="threat.test",
        apparatus_id="test",
        subscribe_channels=["sentiment.in"],
        emit=EmitSpec(channel="threat.out", priority="high"),
        rules=[
            RuleSpec(condition="sentiment_score < -0.3", severity="HIGH", label="neg-signal"),
            RuleSpec(condition="sentiment_score < -0.7", severity="CRITICAL", label="critical-signal"),
        ],
    )
    node = ThreatAssessNode(spec, spine)
    await node.setup()

    output_sub = spine.subscribe("threat.out")

    packet = IntelPacket(
        packet_type="SentimentPacket",
        source_node_id="sentiment.test",
        apparatus_id="test",
        channel="sentiment.in",
        payload={
            "content": "Critical threat",
            "sentiment_score": -0.75,
            "sentiment_confidence": 0.9,
        },
        priority=Priority.HIGH,
    )
    await spine.publish("sentiment.in", packet)
    await node.run()

    result = await asyncio.wait_for(output_sub.get(), timeout=2.0)
    assert result.packet_type == "ThreatPacket"
    assert result.payload["max_severity"] == "CRITICAL"
    assert len(result.payload["threat_matches"]) == 2  # both rules fire

    output_sub.close()
    await node.stop()


@pytest.mark.asyncio
async def test_storage_node_persists_to_sqlite(tmp_path):
    """StorageNode writes IntelPackets to SQLite and they can be read back."""
    from probable_intel.spine.spine import Spine
    from probable_intel.spine.packet import IntelPacket, Priority
    from probable_intel.nexus.spec import NodeSpec
    from probable_intel.nodes.archivists.storage_node import StorageNode

    db_path = str(tmp_path / "test_storage.db")
    spine = Spine()

    spec = NodeSpec(
        node_type="StorageNode",
        node_id="storage.test",
        apparatus_id="test",
        subscribe_channels=["to.store"],
        config={"path": db_path},
    )
    node = StorageNode(spec, spine)
    await node.setup()

    packet = IntelPacket(
        packet_type="ThreatPacket",
        source_node_id="threat.test",
        apparatus_id="test",
        channel="to.store",
        payload={"threat_matches": [{"severity": "HIGH", "label": "test"}], "max_severity": "HIGH"},
        priority=Priority.HIGH,
    )
    await spine.publish("to.store", packet)
    await node.run()

    # Verify persistence
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT id, packet_type, apparatus_id FROM intel_packets").fetchall()
    conn.close()

    assert len(rows) == 1
    assert rows[0][0] == str(packet.packet_id)
    assert rows[0][1] == "ThreatPacket"
    assert rows[0][2] == "test"

    await node.stop()


@pytest.mark.asyncio
async def test_packet_provenance_chain():
    """Provenance chain accumulates correctly through relay hops."""
    from probable_intel.spine.packet import IntelPacket, Priority

    p1 = IntelPacket(
        packet_type="RawFeedPacket",
        source_node_id="feed.source",
        apparatus_id="test",
        channel="raw",
        payload={"content": "test"},
        priority=Priority.NORMAL,
    )
    assert p1.provenance[0] == "feed.source"

    p2 = p1.relay("sentiment.node", "analysis.out", packet_type="SentimentPacket")
    assert p2.provenance[0] == "sentiment.node"
    assert "feed.source" in p2.provenance

    p3 = p2.relay("threat.node", "threat.out", packet_type="ThreatPacket")
    assert p3.provenance[0] == "threat.node"
    assert "sentiment.node" in p3.provenance
    assert "feed.source" in p3.provenance
    assert p3.source_hash == p1.source_hash  # hash preserved through relay
