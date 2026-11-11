"""A node must handle every packet it is sent, whichever of its channels it arrives on."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from probable_intel.hub.factory import NodeFactory
from probable_intel.nexus.spec import EmitSpec, NodeSpec
from probable_intel.nodes.base import BaseNode
from probable_intel.spine.packet import IntelPacket
from probable_intel.spine.spine import Spine

# Every node type that consumes channels (harvesters and the other counter-intel nodes do not).
CONSUMERS = [
    "SentimentNode", "EntityExtractorNode", "ThreatAssessNode", "NarrativeNode", "AttackMapperNode",
    "AlertNode", "AnomalyNode",
    "StorageNode", "KnowledgeGraphNode", "StixExportNode",
    "TaskRouterNode",
    "AttributionNode",
]


async def handles_a_packet(node: BaseNode) -> bool:
    """run() takes one packet; it can only return if there was one left to take."""
    try:
        await asyncio.wait_for(node.run(), timeout=1.0)
    except TimeoutError:
        return False
    return True


@pytest.mark.parametrize("node_type", CONSUMERS)
async def test_packets_waiting_on_two_channels_are_both_handled(node_type: str, tmp_path: Path):
    spine = Spine()
    spec = NodeSpec(
        node_type=node_type, node_id="node", apparatus_id="app",
        subscribe_channels=["in.a", "in.b"], emit=EmitSpec(channel="out"),
        config={"path": str(tmp_path / "packets.db")},  # read by StorageNode only
    )
    node = NodeFactory().create(spec, spine)
    await node.setup()
    try:
        for channel in ("in.a", "in.b"):
            await spine.publish(channel, IntelPacket(
                packet_type="RawPacket", source_node_id="src", apparatus_id="app",
                channel=channel, payload={"content": f"from {channel}"},
            ))

        assert await handles_a_packet(node)
        assert await handles_a_packet(node), "the packet waiting on the other channel was lost"
    finally:
        await node.teardown()
