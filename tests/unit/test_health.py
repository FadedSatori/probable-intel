"""HealthMonitor restarts nodes that fall silent, and only nodes that should be beating."""
from __future__ import annotations

import asyncio
import time

import pytest

from probable_intel.hub.health import HealthMonitor
from probable_intel.hub.lifecycle import NodeLifecycleManager
from probable_intel.hub.registry import NodeRegistry
from probable_intel.nexus.spec import NodeSpec
from probable_intel.nodes.base import BaseNode, NodeState
from probable_intel.spine.spine import Spine


class QuietNode(BaseNode):
    async def run(self) -> None:
        await asyncio.sleep(3600)


class RecordingLifecycle:
    """Stands in for NodeLifecycleManager: records which nodes the monitor asks to restart."""

    def __init__(self) -> None:
        self.restarted: list[str] = []

    async def restart(self, node_id: str) -> None:
        self.restarted.append(node_id)


def register_quiet_node(registry: NodeRegistry) -> QuietNode:
    node = QuietNode(NodeSpec(node_type="QuietNode", node_id="quiet", apparatus_id="app"), Spine())
    registry.register(node)
    return node


async def restarts_requested(registry: NodeRegistry) -> list[str]:
    """Let the monitor run several checks and report which nodes it asked to restart."""
    lifecycle = RecordingLifecycle()
    monitor = HealthMonitor(registry, lifecycle, check_interval=0)  # one check per loop turn
    monitor.start()
    for _ in range(10):
        await asyncio.sleep(0)
    await monitor.stop()
    await asyncio.sleep(0)  # let the last restart the monitor scheduled run
    return lifecycle.restarted


@pytest.mark.parametrize("state", [NodeState.RUNNING, NodeState.IDLE, NodeState.ERROR])
async def test_a_silent_node_that_should_be_beating_is_restarted(state: NodeState):
    registry = NodeRegistry()
    node = register_quiet_node(registry)
    node.state = state
    node._last_heartbeat = time.time() - 3600

    assert "quiet" in await restarts_requested(registry)


@pytest.mark.parametrize("state", [NodeState.STOPPED, NodeState.DRAINING, NodeState.INITIALIZING])
async def test_a_node_that_is_not_meant_to_be_beating_is_left_alone(state: NodeState):
    registry = NodeRegistry()
    node = register_quiet_node(registry)
    node.state = state
    node._last_heartbeat = time.time() - 3600

    assert await restarts_requested(registry) == []


async def test_a_node_stopped_on_purpose_stays_stopped():
    registry = NodeRegistry()
    node = register_quiet_node(registry)
    lifecycle = NodeLifecycleManager(registry)
    await lifecycle.start("quiet")
    await lifecycle.stop("quiet")  # what POST /node/quiet/stop does
    node._last_heartbeat = time.time() - 3600  # an hour goes by

    assert await restarts_requested(registry) == []
