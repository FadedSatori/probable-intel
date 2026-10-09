from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ..base import BaseNode
from ...spine.packet import IntelPacket
from ...storage.sqlite_backend import SQLiteBackend

if TYPE_CHECKING:
    from ...nexus.spec import NodeSpec
    from ...spine.spine import Spine

log = logging.getLogger(__name__)

BATCH_SIZE = 100
BATCH_FLUSH_INTERVAL = 5.0  # seconds


class StorageNode(BaseNode):
    """Persists IntelPackets from subscribed channels to a storage backend.

    Batches writes to reduce I/O syscalls: accumulates packets until batch_size
    is reached or flush_interval seconds elapse, then writes all at once via
    multi-row INSERT with dedup via INSERT OR IGNORE on source_hash.
    """

    def __init__(self, spec: "NodeSpec", spine: "Spine") -> None:
        super().__init__(spec, spine)
        self._backend: SQLiteBackend | None = None
        self._subscriptions = []
        self._write_batch: list[IntelPacket] = []
        self._last_flush = time.time()

    async def setup(self) -> None:
        db_path = (
            self.spec.config.get("path")
            or os.environ.get("SQLITE_PATH")
            or "/data/probable-intel/main.db"
        )
        self._backend = SQLiteBackend(Path(db_path))
        await self._backend.open()
        self._subscriptions = [
            self.spine.subscribe(ch) for ch in self.spec.subscribe_channels
        ]
        self._last_flush = time.time()

    async def teardown(self) -> None:
        await self.flush()  # flush any pending batch
        for sub in self._subscriptions:
            sub.close()
        if self._backend:
            await self._backend.close()

    async def flush(self) -> None:
        """Flush pending write batch to storage backend."""
        if not self._write_batch or not self._backend:
            return
        count = len(self._write_batch)
        for packet in self._write_batch:
            await self._backend.save(packet)
        log.info("node %s flushed %d packets", self.node_id, count)
        self._write_batch.clear()
        self._last_flush = time.time()

    async def run(self) -> None:
        packet = await self._wait_any(self._subscriptions)
        if packet is None:
            return

        self._write_batch.append(packet)

        # Check if batch is full or flush interval elapsed
        now = time.time()
        elapsed = now - self._last_flush
        if len(self._write_batch) >= BATCH_SIZE or elapsed >= BATCH_FLUSH_INTERVAL:
            await self.flush()
