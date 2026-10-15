"""Unit tests for Hub startup/shutdown and directive application."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from probable_intel.hub.hub import Hub
from probable_intel.nodes.base import NodeState
from probable_intel.spine.packet import IntelPacket

_NX = """
apparatus_name: "hub-test"
nodes:
  - type: FeedNode
    id: "feed.alpha"
    targets:
      - feed: "https://example.invalid/feed"
    schedule:
      interval: "30m"
    emit:
      channel: "raw.alpha"
      priority: normal
  - type: ThreatAssessNode
    id: "analyst.threat.alpha"
    subscribe:
      channels: ["raw.alpha"]
    emit:
      channel: "threat.alpha"
      priority: normal
  - type: AnomalyNode
    id: "anomaly.alpha"
    subscribe:
      channels: ["raw.alpha"]
    emit:
      channel: "anomaly.alpha"
      priority: normal
"""


@pytest.fixture
def hub(tmp_path) -> Hub:
    path = tmp_path / "hub-test.nx"
    path.write_text(_NX)
    h = Hub()
    h.load_apparatus(path)
    return h


def _directive(dtype: str, target: str, **params) -> IntelPacket:
    return IntelPacket(
        packet_type="TaskDirectivePacket",
        source_node_id="coordinator.test",
        apparatus_id="hub-test",
        channel="system.task.directives",
        payload={"directive_type": dtype, "target_node_id": target, "parameters": params},
    )


async def test_run_shuts_down_cleanly_when_a_node_setup_fails(hub):
    hub._registry.get("feed.alpha").setup = AsyncMock(side_effect=RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        await hub.run()

    for node in hub._registry.all_nodes():
        assert node.state == NodeState.STOPPED, node.node_id
        assert all(t.done() for t in node._tasks), node.node_id
    assert hub._health._task is not None and hub._health._task.done()


async def test_wildcard_directive_reaches_every_matching_node(hub):
    feed = hub._registry.get("feed.alpha")
    before = feed._interval_seconds

    await hub._apply_directive(_directive("expand_collection", "feed.*"))

    assert feed._interval_seconds == max(30, int(before * 0.5))


async def test_wildcard_directive_skips_non_matching_nodes(hub):
    from probable_intel.spine.packet import Priority

    threat = hub._registry.get("analyst.threat.alpha")
    anomaly = hub._registry.get("anomaly.alpha")
    threat._emit_priority = anomaly._emit_priority = Priority.HIGH

    await hub._apply_directive(_directive("deprioritize", "analyst.*"))

    assert threat._emit_priority == Priority.NORMAL
    assert anomaly._emit_priority == Priority.HIGH


async def test_keyword_filter_directive_appends_to_feed_keyword_list(hub):
    feed = hub._registry.get("feed.alpha")
    feed._keywords = ["breach"]

    await hub._apply_directive(_directive("add_keyword_filter", "feed.alpha", keyword="LockBit"))
    await hub._apply_directive(_directive("add_keyword_filter", "feed.alpha", keyword="lockbit"))

    assert feed._keywords == ["breach", "lockbit"]
