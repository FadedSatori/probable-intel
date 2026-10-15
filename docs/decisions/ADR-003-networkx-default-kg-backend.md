# ADR-003: NetworkX as default KnowledgeGraph backend (Neo4j deferred)

**Status:** Accepted

## Context

KnowledgeGraphNode needed a graph database. Options: Neo4j (persistent, queryable via Cypher), NetworkX (in-memory Python library), SQLite adjacency table.

## Decision

NetworkX is the default and only current backend. Neo4j support is planned (see ROADMAP) but not built.

## Rationale

- **Zero infrastructure** — NetworkX is a pip install; Neo4j requires a running server, authentication, and network access
- **Sufficient for MVP** — the KG is used for entity co-occurrence tracking and ReconNode entity selection. For these use cases, in-memory NetworkX is fast and correct.
- **API isolation** — `KnowledgeGraphNode` exposes `neighbors()`, `path()`, `top_connected()`, `add_node()`, `add_edge()`. The backend is behind this interface; Neo4j can be swapped in without changing callers.
- **Graph survives restarts only when needed** — the KG is rebuilt from the packet stream on each run; persistence is StorageNode's job, not KG's

## Consequences

- Graph is lost on hub restart — acceptable for the current use case
- Max graph size limited by process memory — configurable `max_nodes` in apparatus config (default 5000)
- Neo4j backend must implement the same interface exactly (add_node, add_edge, neighbors, path, top_connected) when built
