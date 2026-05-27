"""Unit tests for ReconNode — autonomous OSINT expansion."""
from __future__ import annotations

import asyncio
import time
import pytest

from probable_intel.nexus.spec import NodeSpec, EmitSpec
from probable_intel.spine.spine import Spine
from probable_intel.spine.packet import IntelPacket, Priority, TrustLevel
from probable_intel.nodes.harvesters.recon_node import ReconNode


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_spec(min_degree=2, max_per_run=5, cooldown_hours=6) -> NodeSpec:
    return NodeSpec(
        node_type="ReconNode",
        node_id="recon.test",
        apparatus_id="test",
        subscribe_channels=["analysis.kg.summary"],
        emit=EmitSpec(channel="raw.recon.test", priority="normal"),
        config={
            "min_entity_degree": min_degree,
            "max_entities_per_run": max_per_run,
            "cooldown_hours": cooldown_hours,
        },
    )


def _kg_packet(entities: list[dict]) -> IntelPacket:
    return IntelPacket(
        packet_type="KGSummaryPacket",
        source_node_id="kg.entities.test",
        apparatus_id="test",
        channel="analysis.kg.summary",
        payload={"top_entities": entities},
        priority=Priority.LOW,
    )


def _entity(text: str, etype: str = "ORG", degree: int = 5, key: str = "") -> dict:
    return {
        "text": text,
        "type": etype,
        "degree": degree,
        "key": key or text.lower().replace(" ", "_"),
    }


_SAMPLE_RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Security News</title>
    <item>
      <title>APT28 linked to new phishing campaign</title>
      <link>https://example.com/news/apt28-phishing</link>
      <description>Researchers identified a new campaign attributed to APT28</description>
    </item>
    <item>
      <title>APT28 exploits zero-day in Windows</title>
      <link>https://example.com/news/apt28-zero-day</link>
      <description>A critical zero-day vulnerability being actively exploited</description>
    </item>
  </channel>
</rss>"""


# ── build_query logic ─────────────────────────────────────────────────────────

def test_build_query_cve_adds_exploit_context():
    node = ReconNode(_make_spec(), Spine())
    q = node._build_query("CVE-2024-1234", "CVE")
    assert "CVE-2024-1234" in q
    assert "exploit" in q.lower() or "vulnerability" in q.lower()


def test_build_query_org_adds_threat_context():
    node = ReconNode(_make_spec(), Spine())
    q = node._build_query("APT28", "ORG")
    assert "APT28" in q
    assert "threat" in q.lower() or "cyber" in q.lower()


def test_build_query_unknown_type_uses_default():
    node = ReconNode(_make_spec(), Spine())
    q = node._build_query("some entity", "UNKNOWN")
    assert "some entity" in q
    assert "cybersecurity" in q.lower() or "threat" in q.lower()


# ── below-threshold: no emit ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_below_min_degree_no_emit(respx_mock):
    """Entities with degree below threshold are ignored — no network call, no emit."""
    spine = Spine()
    node = ReconNode(_make_spec(min_degree=5), spine)
    await node.setup()

    sub = spine.subscribe("raw.recon.test")

    entities = [_entity("APT28", degree=3), _entity("Lazarus", degree=2)]
    await spine.publish("analysis.kg.summary", _kg_packet(entities))
    await node.run()

    assert sub._queue.empty()
    sub.close()
    await node.stop()


@pytest.mark.asyncio
async def test_empty_entities_no_emit(respx_mock):
    """KG summary with no entities → no emit."""
    spine = Spine()
    node = ReconNode(_make_spec(), spine)
    await node.setup()

    sub = spine.subscribe("raw.recon.test")
    await spine.publish("analysis.kg.summary", _kg_packet([]))
    await node.run()

    assert sub._queue.empty()
    sub.close()
    await node.stop()


# ── normal emit flow ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_high_degree_entity_emits_recon_packets(respx_mock):
    """Entity above min_degree threshold → searches Google News and emits RawReconPackets."""
    import re as re_mod
    respx_mock.get(re_mod.compile(r"news\.google\.com")).mock(
        return_value=__import__("httpx").Response(200, content=_SAMPLE_RSS)
    )

    spine = Spine()
    node = ReconNode(_make_spec(min_degree=3), spine)
    await node.setup()

    sub = spine.subscribe("raw.recon.test")
    entities = [_entity("APT28", degree=5)]
    await spine.publish("analysis.kg.summary", _kg_packet(entities))
    await node.run()

    packets = []
    try:
        while True:
            pkt = await asyncio.wait_for(sub.get(), timeout=0.5)
            packets.append(pkt)
    except asyncio.TimeoutError:
        pass

    assert len(packets) == 2
    for pkt in packets:
        assert pkt.packet_type == "RawReconPacket"
        assert pkt.payload["source"] == "google-news-recon"
        assert pkt.payload["recon_entity_text"] == "APT28"
    sub.close()
    await node.stop()


@pytest.mark.asyncio
async def test_recon_packet_has_required_fields(respx_mock):
    """RawReconPacket payload contains all required fields."""
    import re as re_mod
    respx_mock.get(re_mod.compile(r"news\.google\.com")).mock(
        return_value=__import__("httpx").Response(200, content=_SAMPLE_RSS)
    )

    spine = Spine()
    node = ReconNode(_make_spec(), spine)
    await node.setup()

    sub = spine.subscribe("raw.recon.test")
    await spine.publish("analysis.kg.summary", _kg_packet([_entity("APT28", etype="ORG")]))
    await node.run()

    pkt = await asyncio.wait_for(sub.get(), timeout=1.0)
    payload = pkt.payload
    for field in ("title", "content", "url", "source", "recon_entity", "recon_entity_type", "recon_entity_text"):
        assert field in payload, f"missing field: {field}"
    assert payload["url"].startswith("https://")
    sub.close()
    await node.stop()


# ── cooldown ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cooldown_prevents_repeat_search(respx_mock):
    """An entity searched recently is skipped on the next cycle (cooldown active)."""
    import re as re_mod
    call_count = 0

    def handler(request):
        nonlocal call_count
        call_count += 1
        return __import__("httpx").Response(200, content=_SAMPLE_RSS)

    respx_mock.get(re_mod.compile(r"news\.google\.com")).mock(side_effect=handler)

    spine = Spine()
    node = ReconNode(_make_spec(cooldown_hours=1), spine)
    await node.setup()

    entity_list = [_entity("APT28", degree=5)]

    for _ in range(2):
        await spine.publish("analysis.kg.summary", _kg_packet(entity_list))
        await node.run()

    assert call_count == 1, f"Expected 1 search (cooldown), got {call_count}"
    await node.stop()


@pytest.mark.asyncio
async def test_cooldown_zero_allows_repeat(respx_mock):
    """cooldown_hours=0 disables cooldown — same entity searched every cycle."""
    import re as re_mod
    call_count = 0

    def handler(request):
        nonlocal call_count
        call_count += 1
        return __import__("httpx").Response(200, content=_SAMPLE_RSS)

    respx_mock.get(re_mod.compile(r"news\.google\.com")).mock(side_effect=handler)

    spine = Spine()
    node = ReconNode(_make_spec(cooldown_hours=0), spine)
    await node.setup()

    entity_list = [_entity("APT28", degree=5)]
    for _ in range(2):
        await spine.publish("analysis.kg.summary", _kg_packet(entity_list))
        await node.run()

    assert call_count == 2
    await node.stop()


# ── rate limit ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_max_entities_per_run_respected(respx_mock):
    """Only max_entities_per_run entities are searched per run, not more."""
    import re as re_mod
    call_count = 0

    def handler(request):
        nonlocal call_count
        call_count += 1
        return __import__("httpx").Response(200, content=_SAMPLE_RSS)

    respx_mock.get(re_mod.compile(r"news\.google\.com")).mock(side_effect=handler)

    spine = Spine()
    node = ReconNode(_make_spec(max_per_run=2, cooldown_hours=0), spine)
    await node.setup()

    entities = [_entity(f"Entity{i}", degree=5, key=f"entity{i}") for i in range(6)]
    await spine.publish("analysis.kg.summary", _kg_packet(entities))
    await node.run()

    assert call_count == 2, f"Expected 2 searches (max_per_run), got {call_count}"
    await node.stop()


# ── network failure resilience ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_network_failure_no_crash(respx_mock):
    """Network errors are swallowed — no unhandled exception, no emit."""
    import re as re_mod
    import httpx
    respx_mock.get(re_mod.compile(r"news\.google\.com")).mock(
        side_effect=httpx.ConnectError("connection refused")
    )

    spine = Spine()
    node = ReconNode(_make_spec(), spine)
    await node.setup()

    sub = spine.subscribe("raw.recon.test")
    await spine.publish("analysis.kg.summary", _kg_packet([_entity("APT28", degree=5)]))
    await node.run()

    assert sub._queue.empty()
    sub.close()
    await node.stop()


@pytest.mark.asyncio
async def test_bad_rss_response_no_crash(respx_mock):
    """Malformed RSS does not crash the node."""
    import re as re_mod
    respx_mock.get(re_mod.compile(r"news\.google\.com")).mock(
        return_value=__import__("httpx").Response(200, content=b"NOT XML AT ALL")
    )

    spine = Spine()
    node = ReconNode(_make_spec(), spine)
    await node.setup()

    sub = spine.subscribe("raw.recon.test")
    await spine.publish("analysis.kg.summary", _kg_packet([_entity("APT28", degree=5)]))
    await node.run()

    assert sub._queue.empty()
    sub.close()
    await node.stop()


# ── dedup ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_duplicate_urls_deduplicated(respx_mock):
    """Same URL appearing in two searches is only emitted once."""
    import re as re_mod
    respx_mock.get(re_mod.compile(r"news\.google\.com")).mock(
        return_value=__import__("httpx").Response(200, content=_SAMPLE_RSS)
    )

    spine = Spine()
    node = ReconNode(_make_spec(cooldown_hours=0), spine)
    await node.setup()

    sub = spine.subscribe("raw.recon.test")

    # Run twice with same entity (cooldown=0) — same URLs should not be re-emitted
    for _ in range(2):
        await spine.publish("analysis.kg.summary", _kg_packet([_entity("APT28", degree=5, key="apt28_fixed")]))
        await node.run()

    packets = []
    try:
        while True:
            pkt = await asyncio.wait_for(sub.get(), timeout=0.3)
            packets.append(pkt)
    except asyncio.TimeoutError:
        pass

    urls = [p.payload["url"] for p in packets]
    assert len(urls) == len(set(urls)), "Duplicate URLs were emitted"
    sub.close()
    await node.stop()
