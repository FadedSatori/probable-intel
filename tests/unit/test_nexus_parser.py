import warnings
import pytest
from pathlib import Path
from probable_intel.nexus.parser import NexusParser
from probable_intel.nexus.validator import ApparatusValidator
from probable_intel.nexus.errors import NEXUSError, NEXUSWarning

SIMPLE_NX = """
apparatus_name: "test-apparatus"
version: 1.0
description: "Test apparatus"
trust_level: restricted
owner: "test-ops"

nodes:
  - type: FeedNode
    id: "feed.test"
    targets:
      - feed: "https://example.com/feed"
    schedule:
      interval: "15m"
      jitter: 60
    filters:
      keywords: ["breach", "hack"]
      min_word_count: 50
    emit:
      channel: "raw.feed.test"
      priority: high

  - type: StorageNode
    id: "archivist.test"
    subscribe:
      channels: ["raw.feed.test"]
    emit:
      channel: "sink.storage"
      priority: low
"""

CIRCULAR_NX = """
apparatus_name: "circular-test"
version: 1.0
trust_level: unclassified

nodes:
  - type: FeedNode
    id: "node-a"
    targets:
      - feed: "https://example.com/feed"
    subscribe:
      channels: ["ch.b"]
    emit:
      channel: "ch.a"
      priority: normal

  - type: StorageNode
    id: "node-b"
    subscribe:
      channels: ["ch.a"]
    emit:
      channel: "ch.b"
      priority: normal
"""


def test_parse_basic():
    parser = NexusParser()
    spec = parser.parse(SIMPLE_NX)
    assert spec.name == "test-apparatus"
    assert spec.version == 1.0
    assert spec.trust_level == "restricted"
    assert len(spec.nodes) == 2


def test_parse_feed_node():
    parser = NexusParser()
    spec = parser.parse(SIMPLE_NX)
    feed_node = spec.node_by_id("feed.test")
    assert feed_node is not None
    assert feed_node.node_type == "FeedNode"
    assert len(feed_node.targets) == 1
    assert feed_node.targets[0]["url"] == "https://example.com/feed"
    assert feed_node.schedule is not None
    assert feed_node.schedule.interval_seconds == 900  # 15m
    assert feed_node.schedule.jitter_seconds == 60
    assert "breach" in feed_node.filters["keywords"]
    assert feed_node.emit is not None
    assert feed_node.emit.channel == "raw.feed.test"
    assert feed_node.emit.priority == "high"


def test_parse_storage_node():
    parser = NexusParser()
    spec = parser.parse(SIMPLE_NX)
    storage_node = spec.node_by_id("archivist.test")
    assert storage_node is not None
    assert "raw.feed.test" in storage_node.subscribe_channels


def test_validate_valid():
    parser = NexusParser()
    validator = ApparatusValidator()
    spec = parser.parse(SIMPLE_NX)
    validator.validate(spec)  # should not raise


def test_validate_circular_raises():
    parser = NexusParser()
    validator = ApparatusValidator()
    spec = parser.parse(CIRCULAR_NX)
    with pytest.raises(NEXUSError, match="circular"):
        validator.validate(spec)


def test_validate_circular_raises_when_the_channel_has_a_second_emitter():
    # node-a → ch.shared → node-b → ch.loop → node-a, with node-c also emitting on ch.shared.
    nx = """
apparatus_name: "shared-channel-cycle"
version: 1.0
trust_level: unclassified

nodes:
  - type: SentimentNode
    id: "node-a"
    subscribe:
      channels: ["ch.loop"]
    emit:
      channel: "ch.shared"
      priority: normal

  - type: ThreatAssessNode
    id: "node-b"
    subscribe:
      channels: ["ch.shared"]
    emit:
      channel: "ch.loop"
      priority: normal

  - type: FeedNode
    id: "node-c"
    targets:
      - feed: "https://example.com/feed"
    emit:
      channel: "ch.shared"
      priority: normal
"""
    spec = NexusParser().parse(nx)
    with pytest.raises(NEXUSError, match="circular"):
        ApparatusValidator().validate(spec)


def test_two_emitters_on_one_channel_are_not_a_cycle():
    nx = """
apparatus_name: "shared-channel"
version: 1.0
trust_level: unclassified

nodes:
  - type: FeedNode
    id: "feed.a"
    targets:
      - feed: "https://example.com/a"
    emit:
      channel: "raw.shared"
      priority: normal

  - type: FeedNode
    id: "feed.b"
    targets:
      - feed: "https://example.com/b"
    emit:
      channel: "raw.shared"
      priority: normal

  - type: StorageNode
    id: "archivist"
    subscribe:
      channels: ["raw.shared"]
"""
    spec = NexusParser().parse(nx)
    ApparatusValidator().validate(spec)  # should not raise


def test_orphan_channel_warning():
    nx = """
apparatus_name: "orphan-test"
version: 1.0
trust_level: unclassified

nodes:
  - type: FeedNode
    id: "feed.a"
    targets:
      - feed: "https://example.com/feed"
    emit:
      channel: "raw.orphan"
      priority: normal
"""
    parser = NexusParser()
    validator = ApparatusValidator()
    spec = parser.parse(nx)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        validator.validate(spec)
    assert any("raw.orphan" in str(x.message) for x in w)


def test_llm_on_wrong_node_raises():
    nx = """
apparatus_name: "llm-test"
version: 1.0
trust_level: unclassified

nodes:
  - type: FeedNode
    id: "bad.feed"
    targets:
      - feed: "https://example.com/feed"
    backend:
      fallback: "llm"
    emit:
      channel: "raw.bad"
      priority: normal
"""
    parser = NexusParser()
    validator = ApparatusValidator()
    spec = parser.parse(nx)
    with pytest.raises(NEXUSError, match="cannot use LLM"):
        validator.validate(spec)


def test_mvp_demo_file_parses():
    mvp_path = Path(__file__).parent.parent.parent / "nexus" / "apparatuses" / "mvp-demo.nx"
    if not mvp_path.exists():
        pytest.skip("mvp-demo.nx not found")
    parser = NexusParser()
    spec = parser.parse_file(mvp_path)
    assert spec.name == "mvp-demo"
    assert len(spec.nodes) > 0
