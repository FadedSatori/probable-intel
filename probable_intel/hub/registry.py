from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..nodes.base import BaseNode, NodeState
    from ..nexus.spec import NodeSpec


class NodeRegistry:
    def __init__(self) -> None:
        self._nodes: dict[str, "BaseNode"] = {}
        self._specs: dict[str, "NodeSpec"] = {}

    def register(self, node: "BaseNode") -> None:
        self._nodes[node.node_id] = node
        self._specs[node.node_id] = node.spec

    def unregister(self, node_id: str) -> None:
        self._nodes.pop(node_id, None)
        self._specs.pop(node_id, None)

    def get(self, node_id: str) -> "BaseNode | None":
        return self._nodes.get(node_id)

    def all_nodes(self) -> list["BaseNode"]:
        return list(self._nodes.values())

    def all_ids(self) -> list[str]:
        return list(self._nodes.keys())

    def snapshot(self) -> list[dict]:
        return [n.health() for n in self._nodes.values()]
