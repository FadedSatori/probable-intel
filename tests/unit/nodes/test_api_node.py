"""Unit tests for ApiNode."""
from __future__ import annotations

import json
import pytest
import httpx

from probable_intel.nexus.spec import NodeSpec, EmitSpec, ScheduleSpec
from probable_intel.spine.spine import Spine
from probable_intel.nodes.harvesters.api_node import ApiNode, _dig


# ── _dig helper ──────────────────────────────────────────────────────────────

def test_dig_simple_key():
    assert _dig({"id": "CVE-2024-001"}, "id") == "CVE-2024-001"


def test_dig_nested_dict():
    obj = {"cve": {"id": "CVE-2024-001", "descriptions": [{"value": "A critical bug"}]}}
    assert _dig(obj, "cve.id") == "CVE-2024-001"


def test_dig_list_index():
    obj = {"cve": {"descriptions": [{"value": "desc text"}]}}
    assert _dig(obj, "cve.descriptions.0.value") == "desc text"


def test_dig_missing_path_returns_none():
    assert _dig({"a": 1}, "b.c.d") is None


def test_dig_empty_path_returns_obj():
    obj = [{"id": "x"}]
    assert _dig(obj, "") is obj


# ── ApiNode setup & emit ──────────────────────────────────────────────────────

def _make_spec(targets=None, keywords=None) -> NodeSpec:
    spec = NodeSpec(
        node_type="ApiNode",
        node_id="feed.test-api",
        apparatus_id="test",
        emit=EmitSpec(channel="raw.api.test", priority="high"),
        schedule=ScheduleSpec(interval_seconds=3600),
        targets=targets or [],
        filters={"keywords": keywords or []},
    )
    return spec


@pytest.mark.asyncio
async def test_api_node_emits_raw_api_packet(respx_mock):
    """ApiNode fetches JSON and emits a RawApiPacket per item."""
    spine = Spine()
    items = [
        {"id": "CVE-2024-001", "summary": "Critical remote code execution vulnerability"},
        {"id": "CVE-2024-002", "summary": "Minor information disclosure issue"},
    ]
    respx_mock.get("https://api.example.com/vulns").mock(
        return_value=httpx.Response(200, json={"data": items})
    )

    spec = _make_spec(targets=[{
        "type": "api",
        "url": "https://api.example.com/vulns",
        "response_path": "data",
        "id_field": "id",
        "title_field": "id",
        "content_field": "summary",
    }])
    node = ApiNode(spec, spine)
    await node.setup()

    sub = spine.subscribe("raw.api.test")
    await node._fetch(node._targets[0])

    packet = await sub.get()
    assert packet.packet_type == "RawApiPacket"
    assert packet.payload["item_id"] == "CVE-2024-001"
    assert "remote code execution" in packet.payload["content"]
    sub.close()
    await node.teardown()


@pytest.mark.asyncio
async def test_api_node_deduplicates(respx_mock):
    """Items seen on a previous poll are not re-emitted."""
    spine = Spine()
    items = [{"id": "CVE-2024-001", "summary": "A vulnerability"}]
    respx_mock.get("https://api.example.com/vulns").mock(
        return_value=httpx.Response(200, json=items)
    )

    spec = _make_spec(targets=[{
        "type": "api",
        "url": "https://api.example.com/vulns",
        "response_path": "",
        "id_field": "id",
        "title_field": "id",
        "content_field": "summary",
    }])
    node = ApiNode(spec, spine)
    await node.setup()

    sub = spine.subscribe("raw.api.test")
    await node._fetch(node._targets[0])
    await node._fetch(node._targets[0])  # second call — same item

    packet = await sub.get()
    assert packet.payload["item_id"] == "CVE-2024-001"

    # Queue should be empty (item not re-emitted)
    import asyncio
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(sub.get(), timeout=0.1)

    sub.close()
    await node.teardown()


@pytest.mark.asyncio
async def test_api_node_keyword_filter(respx_mock):
    """Items that don't match keyword filter are dropped."""
    spine = Spine()
    items = [
        {"id": "CVE-001", "summary": "critical remote code execution exploit"},
        {"id": "INFO-001", "summary": "minor logging improvement"},
    ]
    respx_mock.get("https://api.example.com/feed").mock(
        return_value=httpx.Response(200, json=items)
    )

    spec = _make_spec(
        keywords=["exploit", "critical"],
        targets=[{
            "type": "api",
            "url": "https://api.example.com/feed",
            "response_path": "",
            "id_field": "id",
            "title_field": "id",
            "content_field": "summary",
        }],
    )
    node = ApiNode(spec, spine)
    await node.setup()

    sub = spine.subscribe("raw.api.test")
    await node._fetch(node._targets[0])

    packet = await sub.get()
    assert packet.payload["item_id"] == "CVE-001"

    import asyncio
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(sub.get(), timeout=0.1)

    sub.close()
    await node.teardown()


@pytest.mark.asyncio
async def test_api_node_handles_fetch_error(respx_mock):
    """Network errors are logged and swallowed — node does not crash."""
    spine = Spine()
    respx_mock.get("https://api.example.com/fail").mock(
        return_value=httpx.Response(500)
    )

    spec = _make_spec(targets=[{
        "type": "api",
        "url": "https://api.example.com/fail",
        "response_path": "items",
        "id_field": "id",
    }])
    node = ApiNode(spec, spine)
    await node.setup()
    # Should not raise
    await node._fetch(node._targets[0])
    await node.teardown()


@pytest.mark.asyncio
async def test_api_node_preset_nvd_merge():
    """NVD preset fields are merged; per-target overrides take precedence."""
    from probable_intel.nodes.harvesters.api_node import _PRESETS
    spine = Spine()
    spec = _make_spec(targets=[{
        "type": "api",
        "url": "https://services.nvd.nist.gov/rest/json/cves/2.0",
        "preset": "nvd",
        "auth_env": "NVD_API_KEY",
    }])
    node = ApiNode(spec, spine)
    await node.setup()
    resolved = node._targets[0]
    assert resolved["response_path"] == _PRESETS["nvd"]["response_path"]
    assert resolved["auth_env"] == "NVD_API_KEY"
    await node.teardown()
