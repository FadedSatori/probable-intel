from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from ..nodes.base import NodeState

if TYPE_CHECKING:
    from .registry import NodeRegistry

log = logging.getLogger(__name__)

MAX_RESTARTS = 3


class NodeLifecycleManager:
    def __init__(self, registry: "NodeRegistry") -> None:
        self._registry = registry
        self._restart_counts: dict[str, int] = {}
        self._restarting: set[str] = set()  # guard against concurrent restart tasks

    async def start(self, node_id: str) -> None:
        node = self._registry.get(node_id)
        if node is None:
            raise KeyError(f"node {node_id!r} not in registry")
        await node.start()

    async def stop(self, node_id: str) -> None:
        node = self._registry.get(node_id)
        if node is None:
            return
        await node.stop()

    async def restart(self, node_id: str) -> None:
        if node_id in self._restarting:
            log.debug("node %s restart already in progress — skipping duplicate", node_id)
            return
        count = self._restart_counts.get(node_id, 0)
        if count >= MAX_RESTARTS:
            log.error(
                "node %s exceeded max restarts (%d); leaving in ERROR state",
                node_id,
                MAX_RESTARTS,
            )
            return
        self._restarting.add(node_id)
        try:
            self._restart_counts[node_id] = count + 1
            log.info("restarting node %s (attempt %d/%d)", node_id, count + 1, MAX_RESTARTS)
            await self.stop(node_id)
            await asyncio.sleep(2 ** count)
            await self.start(node_id)
        finally:
            self._restarting.discard(node_id)

    async def start_all(self) -> None:
        await asyncio.gather(*(self.start(nid) for nid in self._registry.all_ids()))

    async def stop_all(self) -> None:
        await asyncio.gather(*(self.stop(nid) for nid in self._registry.all_ids()))
