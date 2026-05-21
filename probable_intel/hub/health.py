from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .registry import NodeRegistry
    from .lifecycle import NodeLifecycleManager

log = logging.getLogger(__name__)

HEARTBEAT_TIMEOUT_MULTIPLIER = 3


class HealthMonitor:
    def __init__(
        self,
        registry: "NodeRegistry",
        lifecycle: "NodeLifecycleManager",
        check_interval: float = 15.0,
    ) -> None:
        self._registry = registry
        self._lifecycle = lifecycle
        self._check_interval = check_interval
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._monitor_loop(), name="health-monitor")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _monitor_loop(self) -> None:
        while True:
            await asyncio.sleep(self._check_interval)
            now = time.time()
            for node in self._registry.all_nodes():
                threshold = node.HEARTBEAT_INTERVAL * HEARTBEAT_TIMEOUT_MULTIPLIER
                if node._last_heartbeat and (now - node._last_heartbeat) > threshold:
                    log.warning(
                        "node %s missed heartbeat (%.0fs ago); triggering restart",
                        node.node_id,
                        now - node._last_heartbeat,
                    )
                    asyncio.create_task(self._lifecycle.restart(node.node_id))
