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
MAX_RESTARTS_PER_WINDOW = 5
RESTART_WINDOW_SECONDS = 3600.0


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
        self._restart_counts: dict[str, int] = {}    # node_id → restarts in window
        self._restart_window_start: dict[str, float] = {}  # node_id → window start ts

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
                if not (node._last_heartbeat and (now - node._last_heartbeat) > threshold):
                    continue

                node_id = node.node_id

                # Roll the restart window if it has expired
                window_start = self._restart_window_start.get(node_id, 0.0)
                if now - window_start > RESTART_WINDOW_SECONDS:
                    self._restart_counts[node_id] = 0
                    self._restart_window_start[node_id] = now

                count = self._restart_counts.get(node_id, 0)
                if count >= MAX_RESTARTS_PER_WINDOW:
                    log.error(
                        "node %s exceeded %d restarts in %.0fs — stopping restart attempts;"
                        " manual intervention required",
                        node_id, MAX_RESTARTS_PER_WINDOW, RESTART_WINDOW_SECONDS,
                    )
                    continue

                self._restart_counts[node_id] = count + 1
                log.warning(
                    "node %s missed heartbeat (%.0fs ago); triggering restart %d/%d",
                    node_id, now - node._last_heartbeat,
                    count + 1, MAX_RESTARTS_PER_WINDOW,
                )
                asyncio.create_task(self._lifecycle.restart(node_id))
