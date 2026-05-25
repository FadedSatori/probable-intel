"""Unit tests for SocialNode."""
from __future__ import annotations

import asyncio
import json
import pytest
import httpx

from probable_intel.nexus.spec import NodeSpec, EmitSpec, ScheduleSpec
from probable_intel.spine.spine import Spine
from probable_intel.nodes.harvesters.social_node import SocialNode


def _make_spec(targets=None, keywords=None, min_word_count=0) -> NodeSpec:
    return NodeSpec(
        node_type="SocialNode",
        node_id="social.test",
        apparatus_id="test",
        emit=EmitSpec(channel="raw.social.test", priority="normal"),
        schedule=ScheduleSpec(interval_seconds=1200),
        targets=targets or [],
        filters={"keywords": keywords or [], "min_word_count": min_word_count},
    )


# ── Reddit ────────────────────────────────────────────────────────────────────

REDDIT_RESPONSE = {
    "data": {
        "children": [
            {"data": {
                "title": "Critical zero-day exploit found in widespread software",
                "selftext": "Researchers have discovered a critical vulnerability.",
                "permalink": "/r/netsec/comments/abc/test/",
                "author": "security_researcher",
                "score": 500,
                "created_utc": 1700000000,
            }},
            {"data": {
                "title": "Weekend thread",
                "selftext": "",
                "permalink": "/r/netsec/comments/xyz/weekend/",
                "author": "automod",
                "score": 1,
                "created_utc": 1700000001,
            }},
        ]
    }
}


@pytest.mark.asyncio
async def test_reddit_emits_packet(respx_mock):
    """SocialNode fetches Reddit and emits a RawSocialPacket."""
    respx_mock.get("https://www.reddit.com/r/netsec/new.json?limit=25").mock(
        return_value=httpx.Response(200, json=REDDIT_RESPONSE)
    )
    spine = Spine()
    spec = _make_spec(
        keywords=["exploit", "vulnerability", "critical"],
        targets=[{"type": "social", "source": "reddit", "subreddit": "netsec"}],
    )
    node = SocialNode(spec, spine)
    await node.setup()

    sub = spine.subscribe("raw.social.test")
    await node._fetch_reddit(node._targets[0])

    packet = await sub.get()
    assert packet.packet_type == "RawSocialPacket"
    assert packet.payload["source"] == "reddit"
    assert "exploit" in packet.payload["title"].lower() or "exploit" in packet.payload["content"].lower()
    assert packet.payload["subreddit"] == "netsec"
    sub.close()
    await node.teardown()


@pytest.mark.asyncio
async def test_reddit_deduplicates(respx_mock):
    """Same permalink not emitted twice."""
    respx_mock.get("https://www.reddit.com/r/netsec/new.json?limit=25").mock(
        return_value=httpx.Response(200, json=REDDIT_RESPONSE)
    )
    spine = Spine()
    spec = _make_spec(targets=[{"type": "social", "source": "reddit", "subreddit": "netsec"}])
    node = SocialNode(spec, spine)
    await node.setup()

    sub = spine.subscribe("raw.social.test")
    await node._fetch_reddit(node._targets[0])
    count_first = sub._queue.qsize() if hasattr(sub, '_queue') else None

    # Seed all seen_urls from first pass
    initial_seen = len(node._seen_urls)
    await node._fetch_reddit(node._targets[0])
    assert len(node._seen_urls) == initial_seen  # no new unique URLs
    sub.close()
    await node.teardown()


# ── HackerNews ────────────────────────────────────────────────────────────────

HN_RESPONSE = {
    "hits": [
        {
            "objectID": "12345",
            "title": "New ransomware campaign targets healthcare systems",
            "url": "https://example.com/ransomware",
            "author": "hn_user",
            "points": 200,
            "created_at": "2024-01-01T12:00:00Z",
            "story_text": None,
        },
        {
            "objectID": "67890",
            "title": "Ask HN: favorite IDE?",
            "url": None,
            "author": "another_user",
            "points": 10,
            "created_at": "2024-01-01T11:00:00Z",
            "story_text": "Just curious what everyone uses",
        },
    ]
}


@pytest.mark.asyncio
async def test_hackernews_emits_packet(respx_mock):
    """SocialNode fetches HackerNews Algolia API and emits packets."""
    respx_mock.get("https://hn.algolia.com/api/v1/search").mock(
        return_value=httpx.Response(200, json=HN_RESPONSE)
    )
    spine = Spine()
    spec = _make_spec(
        keywords=["ransomware", "vulnerability"],
        targets=[{"type": "social", "source": "hackernews", "query": "ransomware vulnerability"}],
    )
    node = SocialNode(spec, spine)
    await node.setup()

    sub = spine.subscribe("raw.social.test")
    await node._fetch_hackernews(node._targets[0])

    packet = await sub.get()
    assert packet.packet_type == "RawSocialPacket"
    assert packet.payload["source"] == "hackernews"
    assert "ransomware" in packet.payload["title"].lower()
    sub.close()
    await node.teardown()


# ── Mastodon ──────────────────────────────────────────────────────────────────

MASTODON_RESPONSE = [
    {
        "id": "111111",
        "url": "https://infosec.exchange/@user/111111",
        "content": "<p>New <a href='#'>CVE-2024-9999</a> critical RCE in popular library</p>",
        "account": {"acct": "security_user@infosec.exchange"},
        "created_at": "2024-01-01T12:00:00.000Z",
        "reblogs_count": 15,
    }
]


@pytest.mark.asyncio
async def test_mastodon_emits_packet(respx_mock):
    """SocialNode fetches Mastodon hashtag timeline and emits packets."""
    respx_mock.get("https://infosec.exchange/api/v1/timelines/tag/infosec?limit=20").mock(
        return_value=httpx.Response(200, json=MASTODON_RESPONSE)
    )
    spine = Spine()
    spec = _make_spec(targets=[{
        "type": "social",
        "source": "mastodon",
        "instance": "infosec.exchange",
        "hashtag": "infosec",
    }])
    node = SocialNode(spec, spine)
    await node.setup()

    sub = spine.subscribe("raw.social.test")
    await node._fetch_mastodon(node._targets[0])

    packet = await sub.get()
    assert packet.packet_type == "RawSocialPacket"
    assert "mastodon" in packet.payload["source"]
    assert "CVE" in packet.payload["content"]
    assert packet.payload["score"] == 15
    sub.close()
    await node.teardown()


@pytest.mark.asyncio
async def test_mastodon_strips_html_tags(respx_mock):
    """HTML is stripped from Mastodon post content."""
    respx_mock.get("https://infosec.exchange/api/v1/timelines/tag/infosec?limit=20").mock(
        return_value=httpx.Response(200, json=MASTODON_RESPONSE)
    )
    spine = Spine()
    spec = _make_spec(targets=[{
        "type": "social",
        "source": "mastodon",
        "instance": "infosec.exchange",
        "hashtag": "infosec",
    }])
    node = SocialNode(spec, spine)
    await node.setup()

    sub = spine.subscribe("raw.social.test")
    await node._fetch_mastodon(node._targets[0])

    packet = await sub.get()
    assert "<p>" not in packet.payload["content"]
    assert "<a" not in packet.payload["content"]
    sub.close()
    await node.teardown()


# ── Error handling ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_social_node_handles_reddit_error(respx_mock):
    """Network errors from Reddit are swallowed gracefully."""
    respx_mock.get("https://www.reddit.com/r/netsec/new.json?limit=25").mock(
        return_value=httpx.Response(429)
    )
    spine = Spine()
    spec = _make_spec(targets=[{"type": "social", "source": "reddit", "subreddit": "netsec"}])
    node = SocialNode(spec, spine)
    await node.setup()
    await node._fetch_reddit(node._targets[0])  # should not raise
    await node.teardown()
