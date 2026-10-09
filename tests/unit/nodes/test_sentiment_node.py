"""Unit tests for SentimentNode (VADER + LLM fallback)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from probable_intel.nodes.analysts.sentiment_node import SentimentNode
from probable_intel.spine.packet import IntelPacket, Priority, TrustLevel
from probable_intel.spine.spine import Spine
from probable_intel.nexus.spec import NodeSpec, EmitSpec, LLMSpec, BackendSpec


@pytest.fixture
def mock_spine():
    """Mock Spine for testing."""
    spine = MagicMock(spec=Spine)
    spine.subscribe = MagicMock(return_value=MagicMock())
    spine.publish = AsyncMock()
    return spine


@pytest.fixture
def node_spec_vader_only():
    """NodeSpec for VADER-only sentiment analysis (no LLM)."""
    return NodeSpec(
        node_type="SentimentNode",
        node_id="sentiment.test",
        apparatus_id="test-apparatus",
        subscribe_channels=["raw.feed"],
        emit=EmitSpec(channel="sentiment.output", priority="normal"),
        llm=None,
        backend=BackendSpec(name="vader", config={"llm_threshold": 0.4}),
    )


@pytest.fixture
def node_spec_with_llm():
    """NodeSpec for sentiment with LLM fallback."""
    return NodeSpec(
        node_type="SentimentNode",
        node_id="sentiment.test",
        apparatus_id="test-apparatus",
        subscribe_channels=["raw.feed"],
        emit=EmitSpec(channel="sentiment.output", priority="normal"),
        llm=LLMSpec(
            provider="ollama",
            model="llama2",
            base_url="http://localhost:11434",
        ),
        backend=BackendSpec(name="vader", config={"llm_threshold": 0.4}),
    )


@pytest.mark.asyncio
async def test_sentiment_node_vader_positive_sentiment(mock_spine, node_spec_vader_only):
    """Test VADER sentiment analysis on positive text."""
    node = SentimentNode(node_spec_vader_only, mock_spine)
    await node.setup()

    # Positive sentiment packet
    packet = IntelPacket(
        packet_type="RawFeedPacket",
        source_node_id="feed.test",
        apparatus_id="test-apparatus",
        channel="raw.feed",
        payload={"content": "This is absolutely wonderful and amazing!", "title": "Good news"},
        trust_level=TrustLevel.UNCLASSIFIED,
        priority=Priority.NORMAL,
    )

    await node._process(packet)

    # Verify publish was called with SentimentPacket
    mock_spine.publish.assert_called_once()
    call_args = mock_spine.publish.call_args
    emitted_packet = call_args[0][1]

    assert emitted_packet.packet_type == "SentimentPacket"
    assert emitted_packet.payload["sentiment_score"] > 0.5
    assert emitted_packet.payload["backend_used"] == "vader"

    await node.teardown()


@pytest.mark.asyncio
async def test_sentiment_node_vader_negative_sentiment(mock_spine, node_spec_vader_only):
    """Test VADER sentiment analysis on negative text."""
    node = SentimentNode(node_spec_vader_only, mock_spine)
    await node.setup()

    packet = IntelPacket(
        packet_type="RawFeedPacket",
        source_node_id="feed.test",
        apparatus_id="test-apparatus",
        channel="raw.feed",
        payload={"content": "This is terrible, awful, and absolutely horrible!", "title": "Bad news"},
        trust_level=TrustLevel.UNCLASSIFIED,
        priority=Priority.NORMAL,
    )

    await node._process(packet)

    mock_spine.publish.assert_called_once()
    emitted_packet = mock_spine.publish.call_args[0][1]

    assert emitted_packet.packet_type == "SentimentPacket"
    assert emitted_packet.payload["sentiment_score"] < -0.5
    assert emitted_packet.payload["backend_used"] == "vader"

    await node.teardown()


@pytest.mark.asyncio
async def test_sentiment_node_vader_neutral_sentiment(mock_spine, node_spec_vader_only):
    """Test VADER sentiment analysis on neutral text."""
    node = SentimentNode(node_spec_vader_only, mock_spine)
    await node.setup()

    packet = IntelPacket(
        packet_type="RawFeedPacket",
        source_node_id="feed.test",
        apparatus_id="test-apparatus",
        channel="raw.feed",
        payload={"content": "The price of milk is three dollars.", "title": "Neutral fact"},
        trust_level=TrustLevel.UNCLASSIFIED,
        priority=Priority.NORMAL,
    )

    await node._process(packet)

    mock_spine.publish.assert_called_once()
    emitted_packet = mock_spine.publish.call_args[0][1]

    assert emitted_packet.packet_type == "SentimentPacket"
    assert -0.1 < emitted_packet.payload["sentiment_score"] < 0.1
    assert emitted_packet.payload["backend_used"] == "vader"

    await node.teardown()


@pytest.mark.asyncio
async def test_sentiment_node_llm_fallback_on_low_confidence(mock_spine, node_spec_with_llm):
    """Test LLM fallback when VADER confidence is below threshold."""
    node = SentimentNode(node_spec_with_llm, mock_spine)

    # Mock LLM router
    with patch("probable_intel.nodes.analysts.sentiment_node.LLMRouter") as mock_llm_class:
        mock_llm_router = AsyncMock()
        mock_llm_router.complete = AsyncMock(return_value="0.75")
        mock_llm_class.from_spec.return_value = mock_llm_router

        await node.setup()

        # Ambiguous text with low VADER confidence (|compound| < threshold)
        packet = IntelPacket(
            packet_type="RawFeedPacket",
            source_node_id="feed.test",
            apparatus_id="test-apparatus",
            channel="raw.feed",
            payload={"content": "The meeting was interesting.", "title": "Ambiguous"},
            trust_level=TrustLevel.UNCLASSIFIED,
            priority=Priority.NORMAL,
        )

        await node._process(packet)

        # LLM should have been called for fallback
        mock_llm_router.complete.assert_called_once()

        # Verify emitted packet uses LLM score
        mock_spine.publish.assert_called_once()
        emitted_packet = mock_spine.publish.call_args[0][1]
        assert emitted_packet.payload["sentiment_score"] == 0.75
        assert emitted_packet.payload["backend_used"] == "llm"

        await node.teardown()


@pytest.mark.asyncio
async def test_sentiment_node_llm_fallback_invalid_response(mock_spine, node_spec_with_llm):
    """Test LLM fallback when LLM returns invalid score (falls back to VADER)."""
    node = SentimentNode(node_spec_with_llm, mock_spine)

    with patch("probable_intel.nodes.analysts.sentiment_node.LLMRouter") as mock_llm_class:
        mock_llm_router = AsyncMock()
        mock_llm_router.complete = AsyncMock(return_value="not a number")
        mock_llm_class.from_spec.return_value = mock_llm_router

        await node.setup()

        packet = IntelPacket(
            packet_type="RawFeedPacket",
            source_node_id="feed.test",
            apparatus_id="test-apparatus",
            channel="raw.feed",
            payload={"content": "The meeting was interesting.", "title": "Ambiguous"},
            trust_level=TrustLevel.UNCLASSIFIED,
            priority=Priority.NORMAL,
        )

        await node._process(packet)

        # Should fall back to VADER score when LLM fails
        mock_spine.publish.assert_called_once()
        emitted_packet = mock_spine.publish.call_args[0][1]
        assert emitted_packet.payload["backend_used"] == "vader"

        await node.teardown()


@pytest.mark.asyncio
async def test_sentiment_node_skips_empty_content(mock_spine, node_spec_vader_only):
    """Test that SentimentNode skips packets with missing/empty content."""
    node = SentimentNode(node_spec_vader_only, mock_spine)
    await node.setup()

    # Packet with no content field
    packet = IntelPacket(
        packet_type="RawFeedPacket",
        source_node_id="feed.test",
        apparatus_id="test-apparatus",
        channel="raw.feed",
        payload={"title": "No content"},
        trust_level=TrustLevel.UNCLASSIFIED,
        priority=Priority.NORMAL,
    )

    await node._process(packet)

    # Should not publish anything
    mock_spine.publish.assert_not_called()

    await node.teardown()


@pytest.mark.asyncio
async def test_sentiment_node_preserves_tags_and_metadata(mock_spine, node_spec_vader_only):
    """Test that SentimentNode preserves tags and metadata in output packet."""
    node = SentimentNode(node_spec_vader_only, mock_spine)
    await node.setup()

    packet = IntelPacket(
        packet_type="RawFeedPacket",
        source_node_id="feed.test",
        apparatus_id="test-apparatus",
        channel="raw.feed",
        payload={"content": "Great news!", "title": "Good"},
        tags=["security", "critical"],
        trust_level=TrustLevel.RESTRICTED,
        priority=Priority.HIGH,
    )

    await node._process(packet)

    mock_spine.publish.assert_called_once()
    emitted_packet = mock_spine.publish.call_args[0][1]

    # Verify tags preserved
    assert "security" in emitted_packet.tags
    assert "critical" in emitted_packet.tags
    assert emitted_packet.trust_level == TrustLevel.RESTRICTED
    assert emitted_packet.priority == Priority.HIGH

    await node.teardown()
