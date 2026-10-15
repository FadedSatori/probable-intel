"""Unit tests for WebNode."""
from __future__ import annotations

import asyncio

import httpx

from probable_intel.nexus.spec import EmitSpec, NodeSpec
from probable_intel.nodes.harvesters.web_node import WebNode
from probable_intel.spine.spine import Spine

URL = "https://status.example.test/page"


def _make_node(spine: Spine, targets=None) -> WebNode:
    spec = NodeSpec(
        node_type="WebNode",
        node_id="web.test",
        apparatus_id="test",
        targets=targets if targets is not None else [{"type": "web", "url": URL}],
        emit=EmitSpec(channel="raw.web.test", priority="normal"),
    )
    return WebNode(spec, spine)


async def _drain(sub, timeout: float = 0.1) -> list:
    packets = []
    while True:
        try:
            packets.append(await asyncio.wait_for(sub.get(), timeout))
        except asyncio.TimeoutError:
            return packets


async def test_page_is_emitted_as_raw_web_packet(respx_mock):
    respx_mock.get(URL).mock(return_value=httpx.Response(
        200, text="<html>advisory</html>", headers={"content-type": "text/html"}
    ))
    spine = Spine()
    node = _make_node(spine)
    await node.setup()
    sub = spine.subscribe("raw.web.test")

    await node._fetch({"type": "web", "url": URL})

    [packet] = await _drain(sub)
    assert packet.packet_type == "RawWebPacket"
    assert packet.payload == {
        "url": URL,
        "status_code": 200,
        "content_type": "text/html",
        "body": "<html>advisory</html>",
    }
    await node.teardown()


async def test_unchanged_page_is_not_re_emitted_but_changed_page_is(respx_mock):
    respx_mock.get(URL).side_effect = [
        httpx.Response(200, text="v1"),
        httpx.Response(200, text="v1"),
        httpx.Response(200, text="v2"),
    ]
    spine = Spine()
    node = _make_node(spine)
    await node.setup()
    sub = spine.subscribe("raw.web.test")

    for _ in range(3):
        await node._fetch({"type": "web", "url": URL})

    assert [p.payload["body"] for p in await _drain(sub)] == ["v1", "v2"]
    await node.teardown()


async def test_http_error_opens_circuit_after_five_failures(respx_mock):
    route = respx_mock.get(URL).mock(return_value=httpx.Response(503))
    spine = Spine()
    node = _make_node(spine)
    await node.setup()
    sub = spine.subscribe("raw.web.test")

    for _ in range(6):
        await node._fetch({"type": "web", "url": URL})

    assert await _drain(sub) == []
    assert route.call_count == 5
    assert node._cb_check(URL)
    await node.teardown()


async def test_body_is_truncated_to_50k_chars(respx_mock):
    respx_mock.get(URL).mock(return_value=httpx.Response(200, text="x" * 60_000))
    spine = Spine()
    node = _make_node(spine)
    await node.setup()
    sub = spine.subscribe("raw.web.test")

    await node._fetch({"type": "web", "url": URL})

    [packet] = await _drain(sub)
    assert len(packet.payload["body"]) == 50_000
    await node.teardown()


async def test_only_web_targets_are_scraped():
    node = _make_node(Spine(), targets=[
        {"type": "web", "url": URL},
        {"type": "feed", "url": "https://feeds.example.test/rss"},
    ])
    await node.setup()
    assert node._targets == [{"type": "web", "url": URL}]
    await node.teardown()
