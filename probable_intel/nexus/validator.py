from __future__ import annotations

import warnings
from collections import defaultdict

from .errors import NEXUSError, NEXUSWarning
from .spec import ApparatusSpec, NodeSpec

# Node types that may use LLM directives
_LLM_ALLOWED_TYPES = {
    "SentimentNode", "EntityExtractorNode", "NarrativeNode", "AttributionNode"
}

# Sinks that are exempt from "orphan channel" checks (write externally)
_SINK_NODE_TYPES = {"AlertNode", "StorageNode"}


class ApparatusValidator:
    def validate(self, spec: ApparatusSpec) -> None:
        """Raises NEXUSError on fatal issues; emits NEXUSWarning for non-fatal ones."""
        self._check_node_ids_unique(spec)
        self._check_llm_placement(spec)
        self._check_circular_routes(spec)
        self._check_orphan_channels(spec)
        self._check_trust_level_consistency(spec)

    def _check_node_ids_unique(self, spec: ApparatusSpec) -> None:
        seen: set[str] = set()
        for node in spec.nodes:
            if node.node_id in seen:
                raise NEXUSError(
                    f"duplicate node id {node.node_id!r}",
                    apparatus_name=spec.name,
                )
            seen.add(node.node_id)

    def _check_llm_placement(self, spec: ApparatusSpec) -> None:
        for node in spec.nodes:
            has_llm_backend = node.backend.get("fallback") == "llm"
            if has_llm_backend and node.node_type not in _LLM_ALLOWED_TYPES:
                raise NEXUSError(
                    f"node {node.node_id!r} (type {node.node_type}) cannot use LLM backend; "
                    f"only {sorted(_LLM_ALLOWED_TYPES)} are allowed",
                    apparatus_name=spec.name,
                )

    def _check_circular_routes(self, spec: ApparatusSpec) -> None:
        # Build adjacency: channel → set of emitting nodes; node → subscribed channels
        emit_map: dict[str, str] = {}  # channel → emitting node_id
        sub_map: dict[str, list[str]] = defaultdict(list)  # node_id → subscribed channels

        for node in spec.nodes:
            if node.emits_channel:
                emit_map[node.emits_channel] = node.node_id
            for ch in node.subscribe_channels:
                sub_map[node.node_id].append(ch)

        # DFS cycle detection
        def _dfs(node_id: str, path: list[str]) -> None:
            if node_id in path:
                cycle = " → ".join(path[path.index(node_id):] + [node_id])
                raise NEXUSError(
                    f"circular route detected: {cycle}",
                    apparatus_name=spec.name,
                )
            for ch in sub_map.get(node_id, []):
                upstream_node = emit_map.get(ch)
                if upstream_node:
                    _dfs(upstream_node, path + [node_id])

        for node in spec.nodes:
            _dfs(node.node_id, [])

    def _check_orphan_channels(self, spec: ApparatusSpec) -> None:
        emitted = spec.emitting_channels()
        subscribed = spec.subscribed_channels()

        for ch in emitted - subscribed:
            # Sink nodes intentionally emit to external channels — that's fine
            emitting_node = next(
                (n for n in spec.nodes if n.emits_channel == ch), None
            )
            if emitting_node and emitting_node.node_type in _SINK_NODE_TYPES:
                continue
            warnings.warn(
                f"[{spec.name}] channel {ch!r} is emitted but not subscribed within this apparatus",
                NEXUSWarning,
                stacklevel=4,
            )

        for ch in subscribed - emitted:
            warnings.warn(
                f"[{spec.name}] channel {ch!r} is subscribed but not emitted within this apparatus",
                NEXUSWarning,
                stacklevel=4,
            )

    def _check_trust_level_consistency(self, spec: ApparatusSpec) -> None:
        trust_order = {"unclassified": 0, "restricted": 1, "classified": 2, "top_secret": 3}
        apparatus_trust = trust_order.get(spec.trust_level, 0)

        for node in spec.nodes:
            if node.emits_channel:
                # In future: channel-level trust annotations; for now just validate apparatus level
                pass
            # Nodes that subscribe to external channels at higher trust are flagged
            _ = apparatus_trust  # placeholder for future cross-apparatus trust checks
