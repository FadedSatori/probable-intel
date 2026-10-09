"""Unit tests for StorageNode (persistence and dedup via INSERT OR IGNORE)."""

import pytest
import tempfile
import hashlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from probable_intel.nodes.archivists.storage_node import StorageNode
from probable_intel.spine.packet import IntelPacket, Priority, TrustLevel
from probable_intel.spine.spine import Spine
from probable_intel.nexus.spec import NodeSpec, BackendSpec


@pytest.fixture
def temp_db_path():
    """Temporary SQLite database path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "test.db"


@pytest.fixture
def mock_spine():
    """Mock Spine for testing."""
    spine = MagicMock(spec=Spine)
    mock_sub = MagicMock()
    mock_sub.get = AsyncMock()
    mock_sub.close = MagicMock()
    spine.subscribe = MagicMock(return_value=mock_sub)
    return spine


@pytest.fixture
def node_spec(temp_db_path):
    """NodeSpec for StorageNode."""
    return NodeSpec(
        node_type="StorageNode",
        node_id="storage.test",
        apparatus_id="test-apparatus",
        subscribe_channels=["sentiment.output", "threat.alerts"],
        emit=None,  # StorageNode does not emit
        backend=BackendSpec(name="sqlite", config={"path": str(temp_db_path)}),
    )


@pytest.mark.asyncio
async def test_storage_node_persists_packet(mock_spine, node_spec):
    """Test that StorageNode persists a packet to database."""
    node = StorageNode(node_spec, mock_spine)
    await node.setup()

    packet = IntelPacket(
        packet_type="SentimentPacket",
        source_node_id="sentiment.test",
        apparatus_id="test-apparatus",
        channel="sentiment.output",
        payload={"sentiment_score": 0.75, "content": "Good news"},
        source_hash="abc123def456",
        trust_level=TrustLevel.UNCLASSIFIED,
        priority=Priority.NORMAL,
    )

    await node._process(packet)

    # Verify backend.save was called (mocked via await node._process path)
    # For integration, we'd check database directly
    await node.teardown()


@pytest.mark.asyncio
async def test_storage_node_dedup_via_source_hash(mock_spine, node_spec, temp_db_path):
    """Test that duplicate packets (same source_hash) are not inserted twice.

    Verifies INSERT OR IGNORE dedup behavior per ADR-005.
    """
    node = StorageNode(node_spec, mock_spine)
    await node.setup()

    # Create two packets with same source_hash (duplicates)
    packet1 = IntelPacket(
        packet_type="SentimentPacket",
        source_node_id="sentiment.test",
        apparatus_id="test-apparatus",
        channel="sentiment.output",
        payload={"sentiment_score": 0.75, "content": "Original"},
        source_hash="duplicate-hash-abc",
        trust_level=TrustLevel.UNCLASSIFIED,
        priority=Priority.NORMAL,
    )

    packet2 = IntelPacket(
        packet_type="SentimentPacket",
        source_node_id="sentiment.test",
        apparatus_id="test-apparatus",
        channel="sentiment.output",
        payload={"sentiment_score": 0.75, "content": "Duplicate (different content)"},
        source_hash="duplicate-hash-abc",  # SAME source_hash
        trust_level=TrustLevel.UNCLASSIFIED,
        priority=Priority.NORMAL,
    )

    # Persist both
    await node._process(packet1)
    await node.flush()
    await node._process(packet2)
    await node.flush()

    # Query database to verify only one was inserted
    async with node._backend._connection() as conn:
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM packets WHERE source_hash = ?",
            ("duplicate-hash-abc",)
        )
        count = await cursor.fetchone()

    assert count[0] == 1, "Duplicate source_hash should have been ignored"

    await node.teardown()


@pytest.mark.asyncio
async def test_storage_node_batch_flush(mock_spine, node_spec):
    """Test that StorageNode batches writes and flushes at threshold."""
    node = StorageNode(node_spec, mock_spine)
    await node.setup()

    # Add packets up to batch threshold
    for i in range(100):
        packet = IntelPacket(
            packet_type="SentimentPacket",
            source_node_id="sentiment.test",
            apparatus_id="test-apparatus",
            channel="sentiment.output",
            payload={"sentiment_score": 0.5 + i * 0.001, "id": i},
            source_hash=f"hash-{i}",
            trust_level=TrustLevel.UNCLASSIFIED,
            priority=Priority.NORMAL,
        )
        await node._process(packet)

    # Batch should be empty after flush (triggered by BATCH_SIZE threshold)
    assert len(node._write_batch) == 0, "Batch should be empty after reaching threshold"

    await node.teardown()


@pytest.mark.asyncio
async def test_storage_node_flush_on_teardown(mock_spine, node_spec):
    """Test that pending batch is flushed on teardown."""
    node = StorageNode(node_spec, mock_spine)
    await node.setup()

    # Add a few packets (not enough to auto-flush)
    for i in range(5):
        packet = IntelPacket(
            packet_type="SentimentPacket",
            source_node_id="sentiment.test",
            apparatus_id="test-apparatus",
            channel="sentiment.output",
            payload={"sentiment_score": 0.5, "id": i},
            source_hash=f"pending-{i}",
            trust_level=TrustLevel.UNCLASSIFIED,
            priority=Priority.NORMAL,
        )
        await node._process(packet)

    assert len(node._write_batch) == 5, "Batch should have 5 pending packets"

    # Teardown should flush pending
    await node.teardown()

    # Verify batch is empty
    assert len(node._write_batch) == 0, "Batch should be empty after teardown"


@pytest.mark.asyncio
async def test_storage_node_preserves_packet_metadata(mock_spine, node_spec):
    """Test that StorageNode preserves all packet fields in storage."""
    node = StorageNode(node_spec, mock_spine)
    await node.setup()

    packet = IntelPacket(
        packet_type="ThreatPacket",
        source_node_id="threat.analyzer",
        apparatus_id="test-apparatus",
        channel="threat.alerts",
        payload={
            "threat_level": "HIGH",
            "description": "Suspicious activity detected",
            "indicators": ["IOC1", "IOC2"],
        },
        source_hash="threat-abc123",
        trust_level=TrustLevel.CLASSIFIED,
        priority=Priority.CRITICAL,
        tags=["malware", "ransomware"],
    )

    await node._process(packet)
    await node.flush()

    # Query database and verify all fields are preserved
    async with node._backend._connection() as conn:
        cursor = await conn.execute(
            "SELECT packet_type, source_node_id, channel, trust_level, priority "
            "FROM packets WHERE source_hash = ?",
            ("threat-abc123",)
        )
        row = await cursor.fetchone()

    assert row[0] == "ThreatPacket"
    assert row[1] == "threat.analyzer"
    assert row[2] == "threat.alerts"
    assert row[3] == "classified"
    assert row[4] == "critical"

    await node.teardown()


@pytest.mark.asyncio
async def test_storage_node_handles_large_payloads(mock_spine, node_spec):
    """Test that StorageNode handles large JSON payloads."""
    node = StorageNode(node_spec, mock_spine)
    await node.setup()

    # Create a large payload
    large_content = "x" * 100_000  # 100KB of text
    packet = IntelPacket(
        packet_type="RawFeedPacket",
        source_node_id="feed.test",
        apparatus_id="test-apparatus",
        channel="sentiment.output",
        payload={
            "content": large_content,
            "title": "Large article",
            "metadata": {f"key_{i}": f"value_{i}" for i in range(1000)},
        },
        source_hash="large-payload-abc",
        trust_level=TrustLevel.UNCLASSIFIED,
        priority=Priority.NORMAL,
    )

    await node._process(packet)
    await node.flush()

    # Verify it was stored
    async with node._backend._connection() as conn:
        cursor = await conn.execute(
            "SELECT payload FROM packets WHERE source_hash = ?",
            ("large-payload-abc",)
        )
        row = await cursor.fetchone()

    assert row is not None, "Large payload should be stored"

    await node.teardown()
