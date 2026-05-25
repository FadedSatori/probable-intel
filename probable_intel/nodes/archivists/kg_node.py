from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..base import BaseNode
from ...spine.packet import IntelPacket, Priority, TrustLevel

if TYPE_CHECKING:
    from ...nexus.spec import NodeSpec
    from ...spine.spine import Spine

log = logging.getLogger(__name__)


class KnowledgeGraphNode(BaseNode):
    """Builds a live entity co-occurrence graph from EntityPackets.

    Each entity extracted by EntityExtractorNode becomes a graph node.
    Entities co-occurring in the same packet are connected by edges whose
    weight increments on each repeat co-occurrence.

    Periodically emits GraphSummaryPackets with top entities by degree
    centrality and top connections by edge weight.

    Config keys (all optional):
        persist_path          Path to JSON file for graph persistence across restarts
        emit_interval_seconds How often to emit a GraphSummaryPacket (default 300)
        min_edge_weight       Edges below this weight are pruned on summary (default 2)
        max_nodes             Prune least-connected nodes beyond this count (default 10000)
    """

    def __init__(self, spec: "NodeSpec", spine: "Spine") -> None:
        super().__init__(spec, spine)
        self._subscriptions: list = []
        self._emit_channel: str = ""
        self._emit_priority: Priority = Priority.NORMAL

        self._persist_path: Path | None = None
        self._emit_interval: float = 300.0
        self._min_edge_weight: int = 2
        self._max_nodes: int = 10_000

        self._graph: Any = None  # networkx.Graph
        self._packet_count: int = 0
        self._last_emit: float = 0.0

    async def setup(self) -> None:
        try:
            import networkx as nx
            self._graph = nx.Graph()
        except ImportError:
            log.warning(
                "node %s: networkx not installed; KnowledgeGraphNode disabled. "
                "Install with: pip install networkx",
                self.node_id,
            )
            self._graph = None

        cfg = self.spec.config
        persist = cfg.get("persist_path", "")
        if persist:
            self._persist_path = Path(persist)
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            self._load_graph()

        self._emit_interval = float(cfg.get("emit_interval_seconds", 300))
        self._min_edge_weight = int(cfg.get("min_edge_weight", 2))
        self._max_nodes = int(cfg.get("max_nodes", 10_000))

        if self.spec.emit:
            self._emit_channel = self.spec.emit.channel
            self._emit_priority = Priority[self.spec.emit.priority.upper()]

        self._subscriptions = [
            self.spine.subscribe(ch) for ch in self.spec.subscribe_channels
        ]
        self._last_emit = time.time()

    async def teardown(self) -> None:
        for sub in self._subscriptions:
            sub.close()
        self._save_graph()

    async def run(self) -> None:
        if not self._subscriptions:
            await asyncio.sleep(1)
            return

        tasks = [asyncio.create_task(sub.get()) for sub in self._subscriptions]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()

        packet: IntelPacket = next(iter(done)).result()
        await self._ingest(packet)

        now = time.time()
        if self._emit_channel and (now - self._last_emit) >= self._emit_interval:
            await self._emit_summary(packet)
            self._last_emit = now

    async def _ingest(self, packet: IntelPacket) -> None:
        if self._graph is None:
            return

        entities = packet.payload.get("entities", [])
        if not entities:
            return

        self._packet_count += 1

        # Add/update nodes
        node_keys = []
        for ent in entities:
            text = str(ent.get("text", "")).strip()
            etype = str(ent.get("type", "UNKNOWN"))
            if not text:
                continue
            key = f"{etype}:{text}"
            node_keys.append(key)

            if self._graph.has_node(key):
                self._graph.nodes[key]["count"] += 1
                self._graph.nodes[key]["last_seen"] = packet.timestamp_utc.isoformat()
            else:
                self._graph.add_node(key, text=text, entity_type=etype, count=1,
                                     first_seen=packet.timestamp_utc.isoformat(),
                                     last_seen=packet.timestamp_utc.isoformat())

        # Add/update edges between all co-occurring entities
        for i in range(len(node_keys)):
            for j in range(i + 1, len(node_keys)):
                a, b = node_keys[i], node_keys[j]
                if self._graph.has_edge(a, b):
                    self._graph[a][b]["weight"] += 1
                else:
                    self._graph.add_edge(a, b, weight=1)

        # Prune if oversized — drop lowest-degree nodes
        if self._graph.number_of_nodes() > self._max_nodes:
            degrees = sorted(self._graph.degree(), key=lambda x: x[1])
            to_remove = [n for n, _ in degrees[:self._graph.number_of_nodes() - self._max_nodes]]
            self._graph.remove_nodes_from(to_remove)
            log.debug("node %s: pruned %d low-degree nodes", self.node_id, len(to_remove))

        log.debug(
            "node %s: ingested %d entities → graph has %d nodes / %d edges",
            self.node_id, len(node_keys),
            self._graph.number_of_nodes(), self._graph.number_of_edges(),
        )

    async def _emit_summary(self, trigger_packet: IntelPacket) -> None:
        if self._graph is None or not self._emit_channel:
            return

        import networkx as nx

        # Prune weak edges for the summary view
        strong_edges = [
            (u, v, d) for u, v, d in self._graph.edges(data=True)
            if d.get("weight", 1) >= self._min_edge_weight
        ]
        strong_g = self._graph.edge_subgraph([e[:2] for e in strong_edges]) if strong_edges else self._graph

        # Top entities by degree
        top_entities = sorted(
            [
                {
                    "key": n,
                    "text": self._graph.nodes[n].get("text", n),
                    "type": self._graph.nodes[n].get("entity_type", "UNKNOWN"),
                    "count": self._graph.nodes[n].get("count", 0),
                    "degree": self._graph.degree(n),
                }
                for n in self._graph.nodes()
            ],
            key=lambda x: x["degree"],
            reverse=True,
        )[:20]

        # Top edges by weight
        top_connections = sorted(
            [
                {
                    "source": u,
                    "target": v,
                    "weight": d.get("weight", 1),
                }
                for u, v, d in self._graph.edges(data=True)
            ],
            key=lambda x: x["weight"],
            reverse=True,
        )[:20]

        out = trigger_packet.relay(
            self.node_id,
            self._emit_channel,
            packet_type="GraphSummaryPacket",
            payload={
                "node_count": self._graph.number_of_nodes(),
                "edge_count": self._graph.number_of_edges(),
                "strong_edge_count": len(strong_edges),
                "packet_count": self._packet_count,
                "top_entities": top_entities,
                "top_connections": top_connections,
                "min_edge_weight_filter": self._min_edge_weight,
            },
            priority=self._emit_priority,
        )
        await self.emit(self._emit_channel, out)
        log.info(
            "node %s: graph summary — %d nodes, %d edges, %d strong",
            self.node_id,
            self._graph.number_of_nodes(),
            self._graph.number_of_edges(),
            len(strong_edges),
        )

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save_graph(self) -> None:
        if self._graph is None or not self._persist_path:
            return
        try:
            import networkx as nx
            data = {
                "nodes": [
                    {"id": n, **self._graph.nodes[n]}
                    for n in self._graph.nodes()
                ],
                "edges": [
                    {"source": u, "target": v, **d}
                    for u, v, d in self._graph.edges(data=True)
                ],
            }
            self._persist_path.write_text(json.dumps(data, default=str), encoding="utf-8")
            log.info(
                "node %s: graph persisted to %s (%d nodes, %d edges)",
                self.node_id, self._persist_path,
                self._graph.number_of_nodes(), self._graph.number_of_edges(),
            )
        except Exception as e:
            log.error("node %s: failed to save graph: %s", self.node_id, e)

    def _load_graph(self) -> None:
        if self._graph is None or not self._persist_path or not self._persist_path.exists():
            return
        try:
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
            for node in data.get("nodes", []):
                nid = node.pop("id")
                self._graph.add_node(nid, **node)
            for edge in data.get("edges", []):
                self._graph.add_edge(edge["source"], edge["target"],
                                     weight=edge.get("weight", 1))
            log.info(
                "node %s: graph restored from %s (%d nodes, %d edges)",
                self.node_id, self._persist_path,
                self._graph.number_of_nodes(), self._graph.number_of_edges(),
            )
        except Exception as e:
            log.warning("node %s: failed to load graph from %s: %s", self.node_id, self._persist_path, e)

    # ── Public query interface ────────────────────────────────────────────────

    def neighbors(self, entity_key: str) -> list[dict]:
        """Return neighbors of an entity node sorted by edge weight."""
        if self._graph is None or not self._graph.has_node(entity_key):
            return []
        return sorted(
            [
                {
                    "key": n,
                    "text": self._graph.nodes[n].get("text", n),
                    "type": self._graph.nodes[n].get("entity_type", ""),
                    "weight": self._graph[entity_key][n].get("weight", 1),
                }
                for n in self._graph.neighbors(entity_key)
            ],
            key=lambda x: x["weight"],
            reverse=True,
        )

    def path(self, source: str, target: str) -> list[str]:
        """Shortest path between two entity keys. Empty list if no path."""
        if self._graph is None:
            return []
        try:
            import networkx as nx
            return nx.shortest_path(self._graph, source, target)
        except Exception:
            return []

    def top_connected(self, n: int = 10) -> list[dict]:
        """Return top-n entities by degree."""
        if self._graph is None:
            return []
        return sorted(
            [
                {
                    "key": node,
                    "text": self._graph.nodes[node].get("text", node),
                    "type": self._graph.nodes[node].get("entity_type", ""),
                    "degree": deg,
                    "count": self._graph.nodes[node].get("count", 0),
                }
                for node, deg in self._graph.degree()
            ],
            key=lambda x: x["degree"],
            reverse=True,
        )[:n]
