"""Unit tests for KnowledgeGraphNode."""
from __future__ import annotations

import asyncio
import json
import pytest

from probable_intel.nexus.spec import NodeSpec, EmitSpec
from probable_intel.spine.spine import Spine
from probable_intel.spine.packet import IntelPacket, Priority
from probable_intel.nodes.archivists.kg_node import KnowledgeGraphNode

pytest.importorskip("networkx")


def _make_spec(persist_path="", emit_interval=9999, min_edge_weight=1) -> NodeSpec:
    return NodeSpec(
        node_type="KnowledgeGraphNode",
        node_id="kg.test",
        apparatus_id="test",
        subscribe_channels=["analysis.entities.test"],
        emit=EmitSpec(channel="analysis.kg.test", priority="low"),
        config={
            "persist_path": persist_path,
            "emit_interval_seconds": emit_interval,
            "min_edge_weight": min_edge_weight,
            "max_nodes": 1000,
        },
    )


def _entity_packet(entities: list[dict]) -> IntelPacket:
    return IntelPacket(
        packet_type="EntityPacket",
        source_node_id="analyst.entities",
        apparatus_id="test",
        channel="analysis.entities.test",
        payload={
            "content": "test content",
            "entities": entities,
            "entity_count": len(entities),
        },
        priority=Priority.NORMAL,
    )


# ── Graph construction ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_entities_become_graph_nodes():
    """Each unique entity text+type becomes a graph node."""
    spine = Spine()
    spec = _make_spec()
    node = KnowledgeGraphNode(spec, spine)
    await node.setup()

    packet = _entity_packet([
        {"text": "CVE-2024-001", "type": "CVE", "start": 0, "end": 12},
        {"text": "Acme Corp", "type": "ORG", "start": 20, "end": 29},
    ])
    await spine.publish("analysis.entities.test", packet)
    await node.run()

    assert node._graph.number_of_nodes() == 2
    assert node._graph.has_node("CVE:CVE-2024-001")
    assert node._graph.has_node("ORG:Acme Corp")
    await node.stop()


@pytest.mark.asyncio
async def test_co_occurring_entities_get_edge():
    """Entities in the same packet are connected by an edge."""
    spine = Spine()
    node = KnowledgeGraphNode(_make_spec(), spine)
    await node.setup()

    packet = _entity_packet([
        {"text": "RansomGroup", "type": "ORG", "start": 0, "end": 11},
        {"text": "CVE-2024-999", "type": "CVE", "start": 15, "end": 27},
    ])
    await spine.publish("analysis.entities.test", packet)
    await node.run()

    assert node._graph.has_edge("ORG:RansomGroup", "CVE:CVE-2024-999")
    assert node._graph["ORG:RansomGroup"]["CVE:CVE-2024-999"]["weight"] == 1
    await node.stop()


@pytest.mark.asyncio
async def test_repeated_co_occurrence_increments_weight():
    """Edge weight increments each time the same pair co-occurs."""
    spine = Spine()
    node = KnowledgeGraphNode(_make_spec(), spine)
    await node.setup()

    for _ in range(3):
        packet = _entity_packet([
            {"text": "Lazarus", "type": "ORG", "start": 0, "end": 7},
            {"text": "CVE-2024-001", "type": "CVE", "start": 10, "end": 22},
        ])
        await spine.publish("analysis.entities.test", packet)
        await node.run()

    assert node._graph["ORG:Lazarus"]["CVE:CVE-2024-001"]["weight"] == 3
    await node.stop()


@pytest.mark.asyncio
async def test_node_count_increments_on_repeat():
    """Appearance count on a graph node increments each time the entity is seen."""
    spine = Spine()
    node = KnowledgeGraphNode(_make_spec(), spine)
    await node.setup()

    for _ in range(4):
        packet = _entity_packet([{"text": "Log4Shell", "type": "CVE", "start": 0, "end": 8}])
        await spine.publish("analysis.entities.test", packet)
        await node.run()

    assert node._graph.nodes["CVE:Log4Shell"]["count"] == 4
    await node.stop()


@pytest.mark.asyncio
async def test_empty_entities_packet_ignored():
    """Packets with no entities don't change the graph."""
    spine = Spine()
    node = KnowledgeGraphNode(_make_spec(), spine)
    await node.setup()

    packet = _entity_packet([])
    await spine.publish("analysis.entities.test", packet)
    await node.run()

    assert node._graph.number_of_nodes() == 0
    await node.stop()


# ── Query interface ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_neighbors_returns_connected_entities():
    """neighbors() returns entities connected to the queried key."""
    spine = Spine()
    node = KnowledgeGraphNode(_make_spec(), spine)
    await node.setup()

    packet = _entity_packet([
        {"text": "ThreatActor", "type": "ORG", "start": 0, "end": 11},
        {"text": "CVE-001", "type": "CVE", "start": 15, "end": 22},
        {"text": "USA", "type": "GPE", "start": 25, "end": 28},
    ])
    await spine.publish("analysis.entities.test", packet)
    await node.run()

    nbrs = node.neighbors("ORG:ThreatActor")
    assert len(nbrs) == 2
    keys = {n["key"] for n in nbrs}
    assert "CVE:CVE-001" in keys
    assert "GPE:USA" in keys
    await node.stop()


@pytest.mark.asyncio
async def test_top_connected_sorted_by_degree():
    """top_connected() returns entities sorted by degree descending."""
    spine = Spine()
    node = KnowledgeGraphNode(_make_spec(), spine)
    await node.setup()

    # Hub entity "Hub" connected to 3 others
    packet = _entity_packet([
        {"text": "Hub", "type": "ORG", "start": 0, "end": 3},
        {"text": "A", "type": "ORG", "start": 5, "end": 6},
        {"text": "B", "type": "ORG", "start": 8, "end": 9},
        {"text": "C", "type": "ORG", "start": 11, "end": 12},
    ])
    await spine.publish("analysis.entities.test", packet)
    await node.run()

    top = node.top_connected(1)
    assert top[0]["key"] == "ORG:Hub"
    assert top[0]["degree"] == 3
    await node.stop()


@pytest.mark.asyncio
async def test_path_finds_shortest_path():
    """path() returns shortest path between two entity keys."""
    spine = Spine()
    node = KnowledgeGraphNode(_make_spec(), spine)
    await node.setup()

    # A-B edge
    p1 = _entity_packet([
        {"text": "A", "type": "ORG", "start": 0, "end": 1},
        {"text": "B", "type": "ORG", "start": 3, "end": 4},
    ])
    # B-C edge
    p2 = _entity_packet([
        {"text": "B", "type": "ORG", "start": 0, "end": 1},
        {"text": "C", "type": "ORG", "start": 3, "end": 4},
    ])
    for pkt in [p1, p2]:
        await spine.publish("analysis.entities.test", pkt)
        await node.run()

    path = node.path("ORG:A", "ORG:C")
    assert path == ["ORG:A", "ORG:B", "ORG:C"]
    await node.stop()


@pytest.mark.asyncio
async def test_path_returns_empty_when_no_path():
    """path() returns [] when nodes are disconnected."""
    spine = Spine()
    node = KnowledgeGraphNode(_make_spec(), spine)
    await node.setup()

    p1 = _entity_packet([{"text": "A", "type": "ORG", "start": 0, "end": 1}])
    p2 = _entity_packet([{"text": "B", "type": "ORG", "start": 0, "end": 1}])
    for pkt in [p1, p2]:
        await spine.publish("analysis.entities.test", pkt)
        await node.run()

    assert node.path("ORG:A", "ORG:B") == []
    await node.stop()


# ── Persistence ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_graph_persists_and_reloads(tmp_path):
    """Graph is saved to JSON on teardown and restored on next setup."""
    persist = str(tmp_path / "kg.json")
    spine = Spine()

    node1 = KnowledgeGraphNode(_make_spec(persist_path=persist), spine)
    await node1.setup()
    packet = _entity_packet([
        {"text": "EvilCorp", "type": "ORG", "start": 0, "end": 8},
        {"text": "CVE-2024-123", "type": "CVE", "start": 10, "end": 22},
    ])
    await spine.publish("analysis.entities.test", packet)
    await node1.run()
    await node1.stop()  # triggers _save_graph

    # Fresh node, same persist path
    node2 = KnowledgeGraphNode(_make_spec(persist_path=persist), spine)
    await node2.setup()

    assert node2._graph.has_node("ORG:EvilCorp")
    assert node2._graph.has_node("CVE:CVE-2024-123")
    assert node2._graph.has_edge("ORG:EvilCorp", "CVE:CVE-2024-123")
    await node2.stop()


# ── Summary emission ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_summary_emitted_when_interval_elapsed():
    """GraphSummaryPacket emitted when emit_interval elapses."""
    spine = Spine()
    node = KnowledgeGraphNode(_make_spec(emit_interval=0), spine)  # always emit
    await node.setup()

    sub = spine.subscribe("analysis.kg.test")

    packet = _entity_packet([
        {"text": "Actor", "type": "ORG", "start": 0, "end": 5},
        {"text": "CVE-001", "type": "CVE", "start": 7, "end": 14},
    ])
    await spine.publish("analysis.entities.test", packet)
    await node.run()

    result = await asyncio.wait_for(sub.get(), timeout=2.0)
    assert result.packet_type == "GraphSummaryPacket"
    assert result.payload["node_count"] == 2
    assert result.payload["edge_count"] == 1
    assert len(result.payload["top_entities"]) >= 1
    sub.close()
    await node.stop()
