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
    """Manages node lifecycle (start/stop/restart) with restart backoff and concurrency guards.

    Implements exponential backoff on restart (2^attempt seconds) and enforces a maximum
    restart count (MAX_RESTARTS=3) per node. Prevents concurrent restarts of the same node
    via _restarting set guard.
    """

    def __init__(self, registry: "NodeRegistry") -> None:
        """Initialize lifecycle manager with a node registry.

        Args:
            registry: NodeRegistry instance providing access to all nodes.
        """
        self._registry = registry
        self._restart_counts: dict[str, int] = {}
        self._restarting: set[str] = set()  # guard against concurrent restart tasks

    async def start(self, node_id: str) -> None:
        """Start a node by ID. Raises KeyError if node not found in registry."""
        node = self._registry.get(node_id)
        if node is None:
            raise KeyError(f"node {node_id!r} not in registry")
        await node.start()

    async def stop(self, node_id: str) -> None:
        """Stop a node by ID. No-op if node not found."""
        node = self._registry.get(node_id)
        if node is None:
            return
        await node.stop()

    async def restart(self, node_id: str) -> None:
        """Restart a node with exponential backoff and restart count limits.

        Enforces:
        - Max concurrent restarts per node (guarded by _restarting set)
        - Max total restarts per node (MAX_RESTARTS)
        - Exponential backoff: 2^attempt seconds between stop and start

        Does nothing if restart already in progress or limit exceeded.
        """
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
