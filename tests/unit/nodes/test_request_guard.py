"""FingerprintDefenseNode wiring: Hub hands it to harvesters, which report each request."""
from __future__ import annotations

import re
import time

import httpx
import pytest

from probable_intel.hub.hub import Hub
from probable_intel.nexus.spec import EmitSpec, NodeSpec
from probable_intel.nodes.harvesters.api_node import ApiNode
from probable_intel.nodes.harvesters.feed_node import FeedNode
from probable_intel.nodes.harvesters.recon_node import ReconNode
from probable_intel.nodes.harvesters.social_node import SocialNode
from probable_intel.nodes.harvesters.web_node import WebNode
from probable_intel.spine.packet import IntelPacket
from probable_intel.spine.spine import Spine

RSS = (
    b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title><link>https://x.test</link>'
    b"<description>d</description><item><title>APT28 news</title><link>https://x.test/1</link>"
    b"<description>APT28 exploit write-up</description></item></channel></rss>"
)


class _Guard:
    """Records report_request calls into a shared event log."""

    def __init__(self, events: list, delay: float = 0.0) -> None:
        self.events, self.delay, self.calls = events, delay, []

    async def report_request(self, identity_profile_id: str, target_url: str, node_id: str) -> float:
        self.calls.append((identity_profile_id, target_url, node_id))
        self.events.append("guard")
        return self.delay


def _spec(node_type: str, node_id: str, **kwargs) -> NodeSpec:
    return NodeSpec(
        node_type=node_type, node_id=node_id, apparatus_id="test",
        emit=EmitSpec(channel=f"raw.{node_id}", priority="normal"), **kwargs,
    )


def _recon_trigger() -> IntelPacket:
    return IntelPacket(packet_type="GraphSummaryPacket", source_node_id="kg", apparatus_id="test",
                       channel="analysis.kg.summary", payload={})


# (node factory, coroutine that performs one fetch, URL pattern, expected reported URL, response)
HARVESTERS = {
    "feed": (
        lambda s: FeedNode(_spec("FeedNode", "feed.t", targets=[{"type": "feed", "url": "https://f.test/rss"}]), s),
        lambda n: n._fetch_feed("https://f.test/rss"),
        "https://f.test/rss", "https://f.test/rss", httpx.Response(200, content=RSS),
    ),
    "web": (
        lambda s: WebNode(_spec("WebNode", "web.t"), s),
        lambda n: n._fetch({"type": "web", "url": "https://w.test/page"}),
        "https://w.test/page", "https://w.test/page", httpx.Response(200, text="page"),
    ),
    "api": (
        lambda s: ApiNode(_spec("ApiNode", "api.t", targets=[{"type": "api", "url": "https://a.test/v1"}]), s),
        lambda n: n._fetch(n._targets[0]),
        "https://a.test/v1", "https://a.test/v1", httpx.Response(200, json=[]),
    ),
    "hackernews": (
        lambda s: SocialNode(_spec("SocialNode", "social.t"), s),
        lambda n: n._fetch_hackernews({"query": "exploit"}),
        re.compile(r"hn\.algolia\.com"), "https://hn.algolia.com/api/v1/search",
        httpx.Response(200, json={"hits": []}),
    ),
    "mastodon": (
        lambda s: SocialNode(_spec("SocialNode", "social.t"), s),
        lambda n: n._fetch_mastodon({"instance": "infosec.exchange", "hashtag": "cve"}),
        re.compile(r"infosec\.exchange"), "https://infosec.exchange/api/v1/timelines/tag/cve?limit=20",
        httpx.Response(200, json=[]),
    ),
    "recon": (
        lambda s: ReconNode(_spec("ReconNode", "recon.t"), s),
        lambda n: n._search_and_emit("APT28", {"text": "APT28", "type": "ORG", "key": "apt28"}, _recon_trigger()),
        re.compile(r"news\.google\.com"), None, httpx.Response(200, content=RSS),
    ),
}


@pytest.mark.parametrize("name", HARVESTERS)
async def test_harvester_reports_request_to_guard_before_fetching(name, respx_mock):
    make, fetch, pattern, expected_url, response = HARVESTERS[name]
    events: list[str] = []

    def respond(request):
        events.append("http")
        return response

    respx_mock.get(pattern).mock(side_effect=respond)
    node = make(Spine())
    await node.setup()
    guard = _Guard(events)
    node._request_guard = guard

    await fetch(node)

    assert events == ["guard", "http"]
    [(identity, url, node_id)] = guard.calls
    assert identity == f"{node.node_id}/rotate"
    assert node_id == node.node_id
    if expected_url:
        assert url == expected_url
    await node.teardown()


@pytest.mark.parametrize("name", HARVESTERS)
async def test_open_circuit_skips_both_report_and_request(name, respx_mock):
    make, fetch, pattern, _, response = HARVESTERS[name]
    route = respx_mock.get(pattern).mock(return_value=response)
    node = make(Spine())
    await node.setup()
    guard = _Guard([])
    node._request_guard = guard
    node._circuit_open = _AlwaysOpen()

    await fetch(node)

    assert guard.calls == [] and route.call_count == 0
    await node.teardown()


class _AlwaysOpen(dict):
    def get(self, key, default=None):
        return float("inf")


async def test_before_request_sleeps_the_returned_jitter():
    node = WebNode(_spec("WebNode", "web.t"), Spine())
    node._request_guard = _Guard([], delay=0.15)

    start = time.monotonic()
    await node._before_request("https://w.test/page")

    assert time.monotonic() - start >= 0.14


async def test_before_request_without_guard_is_a_no_op():
    node = WebNode(_spec("WebNode", "web.t"), Spine())
    start = time.monotonic()
    await node._before_request("https://w.test/page")
    assert time.monotonic() - start < 0.05


_NX = """
apparatus_name: "{name}"
nodes:
  - type: FeedNode
    id: "feed.{name}"
    targets:
      - feed: "https://f.test/rss"
    emit:
      channel: "raw.{name}"
      priority: normal
  - type: ThreatAssessNode
    id: "analyst.threat.{name}"
    subscribe:
      channels: ["raw.{name}"]
    emit:
      channel: "sink.{name}"
      priority: normal
{extra}"""

_FP = """  - type: FingerprintDefenseNode
    id: "ci.fingerprint.{name}"
"""


async def test_hub_wires_guard_per_apparatus(tmp_path):
    guarded = tmp_path / "guarded.nx"
    guarded.write_text(_NX.format(name="g", extra=_FP.format(name="g")))
    plain = tmp_path / "plain.nx"
    plain.write_text(_NX.format(name="p", extra=""))
    hub = Hub()
    hub.load_apparatus(guarded)
    hub.load_apparatus(plain)
    reg = hub._registry
    guard = reg.get("ci.fingerprint.g")

    assert reg.get("feed.g")._request_guard is guard
    assert guard._request_guard is None  # never guards itself
    assert reg.get("feed.p")._request_guard is None  # other apparatus untouched
