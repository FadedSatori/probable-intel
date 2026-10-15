from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from collections import deque
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
        self._target_errors: dict[str, int] = {}    # key → consecutive error count
        self._circuit_open: dict[str, float] = {}   # key → open_until timestamp
        self._ready_packets: deque[IntelPacket] = deque()  # received by _wait_any, not yet returned
        # FingerprintDefenseNode in this apparatus, wired by Hub; see _before_request().
        self._request_guard = None
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
        if self._ready_packets:
            return self._ready_packets.popleft()
        if not subscriptions:
            await asyncio.sleep(1.0)
            return None
        tasks = [asyncio.create_task(sub.get()) for sub in subscriptions]
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
            raise
        for t in pending:
            t.cancel()  # safe: a cancelled Queue.get() leaves its item in the queue
        # Several gets can finish in the same tick, and each has already dequeued
        # its packet — buffer the extras instead of dropping them.
        packets = [t.result() for t in tasks if t in done]
        self._ready_packets.extend(packets[1:])
        return packets[0]

    async def _before_request(self, url: str) -> None:
        """Call immediately before each outbound collection request.

        With a FingerprintDefenseNode wired in, the request is reported to it
        (it publishes an audit event on ci.opsec.audit for OpSecNode) and the
        jitter it returns is slept, so requests don't fire in a machine-regular
        burst. A no-op when the apparatus has no FingerprintDefenseNode.
        """
        if self._request_guard is None:
            return
        identity = f"{self.node_id}/{self.spec.identity_profile}"
        delay = await self._request_guard.report_request(identity, url, self.node_id)
        if delay > 0:
            await asyncio.sleep(delay)

    # ── per-target circuit breaker ─────────────────────────────────────────

    def _cb_check(self, key: str) -> bool:
        """Return True if the circuit is open (caller should skip this target)."""
        return time.time() < self._circuit_open.get(key, 0.0)

    def _cb_success(self, key: str) -> None:
        self._target_errors.pop(key, None)
        self._circuit_open.pop(key, None)

    def _cb_failure(
        self, key: str, *, max_errors: int = 5, open_seconds: float = 300.0
    ) -> None:
        count = self._target_errors.get(key, 0) + 1
        self._target_errors[key] = count
        if count >= max_errors:
            self._circuit_open[key] = time.time() + open_seconds
            log.warning(
                "node %s: circuit open for %r (%.0fs) after %d consecutive errors",
                self.node_id, key, open_seconds, count,
            )

    async def _handle_fetch_error(
        self, key: str, exc: Exception, max_errors: int = 5, open_seconds: int = 300
    ) -> None:
        """Standardized error handling: log, update circuit breaker, optionally emit error packet.

        Args:
            key: Target/resource identifier for circuit breaker tracking
            exc: The exception that occurred
            max_errors: Threshold for circuit breaker to open
            open_seconds: Duration to keep circuit open
        """
        self._cb_failure(key, max_errors=max_errors, open_seconds=open_seconds)
        log.error("[%s] fetch failed for %r: %s", self.node_id, key, exc)

    def health(self) -> dict:
        """Return node health status dict.

        Returns:
            Dict with keys:
            - node_id: str — unique node identifier
            - state: str — current NodeState name (IDLE, RUNNING, ERROR, etc.)
            - error_count: int — consecutive errors since last success
            - last_heartbeat: float — Unix timestamp of last heartbeat, 0.0 if never
        """
        return {
            "node_id": self.node_id,
            "state": self.state.name,
            "error_count": self._error_count,
            "last_heartbeat": self._last_heartbeat,
        }
