from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from enum import auto, Enum
from typing import TYPE_CHECKING

from ..spine.packet import IntelPacket, Priority

if TYPE_CHECKING:
    from ..nexus.spec import NodeSpec
    from ..spine.spine import Spine

log = logging.getLogger(__name__)


class NodeState(Enum):
    DECLARED = auto()
    INITIALIZING = auto()
    IDLE = auto()
    RUNNING = auto()
    DRAINING = auto()
    ERROR = auto()
    STOPPED = auto()


class BaseNode(ABC):
    """Abstract base for all NEXUS node archetypes."""

    HEARTBEAT_INTERVAL: float = 10.0

    def __init__(self, spec: "NodeSpec", spine: "Spine") -> None:
        self.spec = spec
        self.spine = spine
        self.node_id = spec.node_id
        self.apparatus_id = spec.apparatus_id
        self.state = NodeState.DECLARED
        self._tasks: list[asyncio.Task] = []
        self._error_count = 0
        self._last_heartbeat = 0.0
        self._stop_event = asyncio.Event()
        self._paused_until: float = 0.0  # set by Hub directive to pause this node
        # Emit config — every node reads spec.emit once here; override in setup() if needed
        self._emit_channel: str = spec.emit.channel if spec.emit else ""
        self._emit_priority: Priority = (
            Priority[spec.emit.priority.upper()] if spec.emit else Priority.NORMAL
        )

    # ── lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        self.state = NodeState.INITIALIZING
        log.info("node %s initializing", self.node_id)
        try:
            await self.setup()
        except Exception as e:
            log.error("node %s setup failed: %s", self.node_id, e)
            self.state = NodeState.ERROR
            raise
        self.state = NodeState.IDLE
        self._stop_event.clear()
        self._tasks = [
            asyncio.create_task(self._heartbeat_loop(), name=f"{self.node_id}:heartbeat"),
            asyncio.create_task(self._run_loop(), name=f"{self.node_id}:run"),
        ]
        self.state = NodeState.RUNNING
        log.info("node %s running", self.node_id)

    async def stop(self) -> None:
        self.state = NodeState.DRAINING
        self._stop_event.set()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.teardown()
        self.state = NodeState.STOPPED
        log.info("node %s stopped", self.node_id)

    # ── abstract interface ─────────────────────────────────────────────────

    async def setup(self) -> None:
        """Override to open connections, load models, etc."""

    async def teardown(self) -> None:
        """Override to close connections, flush buffers, etc."""

    @abstractmethod
    async def run(self) -> None:
        """Core node logic. Called in a loop; should block until one unit of work is done."""

    # ── internal loops ─────────────────────────────────────────────────────

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            if time.time() < self._paused_until:
                await asyncio.sleep(1.0)
                continue
            try:
                self.state = NodeState.RUNNING
                await self.run()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._error_count += 1
                log.error("node %s run error #%d: %s", self.node_id, self._error_count, e)
                await asyncio.sleep(min(2 ** self._error_count, 60))
        self.state = NodeState.IDLE

    async def _heartbeat_loop(self) -> None:
        while not self._stop_event.is_set():
            self._last_heartbeat = time.time()
            await self.spine.publish(
                "system.heartbeat",
                self._make_heartbeat_packet(),
            )
            await asyncio.sleep(self.HEARTBEAT_INTERVAL)

    def _make_heartbeat_packet(self) -> "IntelPacket":
        from ..spine.packet import IntelPacket, Priority, TrustLevel

        return IntelPacket(
            packet_type="HeartbeatPacket",
            source_node_id=self.node_id,
            apparatus_id=self.apparatus_id,
            channel="system.heartbeat",
            payload={
                "state": self.state.name,
                "error_count": self._error_count,
            },
            priority=Priority.LOW,
            trust_level=TrustLevel.UNCLASSIFIED,
            ttl_seconds=60,
        )

    # ── helpers ────────────────────────────────────────────────────────────

    async def emit(self, channel: str, packet: IntelPacket) -> None:
        await self.spine.publish(channel, packet)

    async def _wait_any(self, subscriptions: list) -> IntelPacket | None:
        """Block until any subscribed channel delivers a packet.

        Returns None (after a 1-second sleep) when subscriptions is empty, so
        callers can do ``if (packet := await self._wait_any(...)) is None: return``
        and the run loop will yield control without busy-spinning.
        """
        if not subscriptions:
            await asyncio.sleep(1.0)
            return None
        tasks = [asyncio.create_task(sub.get()) for sub in subscriptions]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        return next(iter(done)).result()

    def health(self) -> dict:
        return {
            "node_id": self.node_id,
            "state": self.state.name,
            "error_count": self._error_count,
            "last_heartbeat": self._last_heartbeat,
        }
