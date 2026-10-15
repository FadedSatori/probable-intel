"""Unit tests for FeedNode (RSS + Atom ingestion)."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from probable_intel.nexus.spec import EmitSpec, NodeSpec, ScheduleSpec
from probable_intel.nodes.harvesters import feed_node
from probable_intel.nodes.harvesters.feed_node import FeedNode
from probable_intel.spine.spine import Spine

RSS_URL = "https://feeds.example.test/rss"
ATOM_URL = "https://feeds.example.test/atom"


def _rss(*items: tuple[str, str, str]) -> bytes:
    """items: (title, link, description)."""
    body = "".join(
        f"<item><title>{t}</title><link>{link}</link><description>{d}</description>"
        "<pubDate>Wed, 21 May 2026 12:00:00 +0000</pubDate></item>"
        for t, link, d in items
    )
    return (
        '<?xml version="1.0"?><rss version="2.0"><channel><title>Feed</title>'
        f"<link>https://example.test</link><description>d</description>{body}</channel></rss>"
    ).encode()


ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom</title><id>urn:feed</id>
<updated>2026-05-21T12:00:00Z</updated>
<entry><title>Ransomware hits city</title><id>urn:uuid:1</id>
<updated>2026-05-21T12:00:00Z</updated>
<link rel="alternate" href="https://example.test/atom-1"/>
<summary>A ransomware attack hit the city transit system overnight.</summary></entry>
</feed>"""


def _make_node(spine: Spine, urls=(RSS_URL,), **filters) -> FeedNode:
    spec = NodeSpec(
        node_type="FeedNode",
        node_id="feed.test",
        apparatus_id="test",
        targets=[{"type": "feed", "url": u} for u in urls],
        emit=EmitSpec(channel="raw.feed.test", priority="normal"),
        schedule=ScheduleSpec(interval_seconds=900),
        filters=filters,
    )
    return FeedNode(spec, spine)


async def _drain(sub, timeout: float = 0.1) -> list:
    packets = []
    while True:
        try:
            packets.append(await asyncio.wait_for(sub.get(), timeout))
        except asyncio.TimeoutError:
            return packets


async def test_rss_item_emits_packet_with_link_title_and_iso_date(respx_mock):
    respx_mock.get(RSS_URL).mock(return_value=httpx.Response(
        200, content=_rss(("CVE roundup", "https://example.test/r1", "A new CVE was published."))
    ))
    spine = Spine()
    node = _make_node(spine)
    await node.setup()
    sub = spine.subscribe("raw.feed.test")

    await node._fetch_feed(RSS_URL)

    [packet] = await _drain(sub)
    assert packet.packet_type == "RawFeedPacket"
    assert packet.payload["title"] == "CVE roundup"
    assert packet.payload["url"] == "https://example.test/r1"
    assert packet.payload["content"] == "A new CVE was published."
    assert packet.payload["feed_url"] == RSS_URL
    assert packet.payload["published"].startswith("2026-05-21T12:00:00")
    assert len(packet.source_hash) == 16
    await node.teardown()


async def test_atom_entry_fields_are_plain_text(respx_mock):
    respx_mock.get(ATOM_URL).mock(return_value=httpx.Response(200, content=ATOM))
    spine = Spine()
    node = _make_node(spine, urls=(ATOM_URL,))
    await node.setup()
    sub = spine.subscribe("raw.feed.test")

    await node._fetch_feed(ATOM_URL)

    [packet] = await _drain(sub)
    assert packet.payload["title"] == "Ransomware hits city"
    assert packet.payload["content"] == "A ransomware attack hit the city transit system overnight."
    assert packet.payload["url"] == "https://example.test/atom-1"
    await node.teardown()


async def test_filters_drop_short_excluded_and_off_topic_items(respx_mock):
    long_tail = " ".join(["word"] * 10)
    respx_mock.get(RSS_URL).mock(return_value=httpx.Response(200, content=_rss(
        ("keep", "https://example.test/1", f"Major BREACH disclosed {long_tail}"),
        ("short", "https://example.test/2", "breach"),
        ("excluded", "https://example.test/3", f"breach in a sponsored webinar {long_tail}"),
        ("off-topic", "https://example.test/4", f"quarterly earnings beat estimates {long_tail}"),
    )))
    spine = Spine()
    node = _make_node(
        spine, keywords=["Breach"], exclude_keywords=["Sponsored"], min_word_count=5,
    )
    await node.setup()
    sub = spine.subscribe("raw.feed.test")

    await node._fetch_feed(RSS_URL)

    assert [p.payload["title"] for p in await _drain(sub)] == ["keep"]
    await node.teardown()


async def test_same_content_is_emitted_once_across_fetches_and_feeds(respx_mock):
    body = _rss(("dup", "https://example.test/d", "Identical exploit write-up."))
    other = "https://feeds.example.test/mirror"
    respx_mock.get(RSS_URL).mock(return_value=httpx.Response(200, content=body))
    respx_mock.get(other).mock(return_value=httpx.Response(200, content=body))
    spine = Spine()
    node = _make_node(spine, urls=(RSS_URL, other))
    await node.setup()
    sub = spine.subscribe("raw.feed.test")

    for url in (RSS_URL, RSS_URL, other):
        await node._fetch_feed(url)

    assert len(await _drain(sub)) == 1
    await node.teardown()


async def test_seen_hash_cap_evicts_oldest_first(respx_mock, monkeypatch):
    monkeypatch.setattr(feed_node, "SEEN_HASHES_CAP", 4)
    respx_mock.get(RSS_URL).mock(return_value=httpx.Response(200, content=_rss(
        *[(f"t{i}", f"https://example.test/{i}", f"item number {i}") for i in range(5)]
    )))
    spine = Spine()
    node = _make_node(spine)
    await node.setup()
    sub = spine.subscribe("raw.feed.test")

    await node._fetch_feed(RSS_URL)
    hashes = [p.source_hash for p in await _drain(sub)]

    assert list(node._seen_hashes) == hashes[2:]  # 5 > cap of 4: the 2 oldest evicted
    await node.teardown()


async def test_http_error_counts_toward_circuit_breaker_and_opens_it(respx_mock):
    route = respx_mock.get(RSS_URL).mock(return_value=httpx.Response(500))
    spine = Spine()
    node = _make_node(spine)
    await node.setup()
    sub = spine.subscribe("raw.feed.test")

    for _ in range(6):
        await node._fetch_feed(RSS_URL)

    assert await _drain(sub) == []
    assert route.call_count == 5  # circuit opened after 5 failures; 6th fetch skipped
    assert node._cb_check(RSS_URL)
    await node.teardown()


async def test_unparseable_body_is_a_fetch_error_and_success_resets_it(respx_mock):
    route = respx_mock.get(RSS_URL)
    route.side_effect = [
        httpx.Response(200, content=b"<html>not a feed</html>"),
        httpx.Response(200, content=_rss(("ok", "https://example.test/ok", "recovered"))),
    ]
    spine = Spine()
    node = _make_node(spine)
    await node.setup()

    await node._fetch_feed(RSS_URL)
    assert node._target_errors[RSS_URL] == 1
    await node._fetch_feed(RSS_URL)
    assert RSS_URL not in node._target_errors
    await node.teardown()


@pytest.mark.parametrize("schedule, expected", [(None, 900), (ScheduleSpec(interval_seconds=60), 60)])
async def test_interval_comes_from_schedule(schedule, expected):
    node = _make_node(Spine())
    node.spec.schedule = schedule
    await node.setup()
    assert node._interval_seconds == expected
    await node.teardown()
