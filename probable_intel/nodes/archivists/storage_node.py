from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from ..base import BaseNode
from ...storage.sqlite_backend import SQLiteBackend
from ...storage.vector_backend import DEFAULT_MODEL, Embedder, VectorIndex, load_embedder

if TYPE_CHECKING:
    from ...nexus.spec import NodeSpec
    from ...spine.packet import IntelPacket
    from ...spine.spine import Spine

log = logging.getLogger(__name__)


class StorageNode(BaseNode):
    """Persists IntelPackets from subscribed channels to a storage backend.

    With the [semantic] extra installed it also embeds each article's content
    (once per source_hash) for `pi search`; config `vector_index: false` opts out.
    """

    def __init__(self, spec: "NodeSpec", spine: "Spine") -> None:
        super().__init__(spec, spine)
        self._backend: SQLiteBackend | None = None
        self._subscriptions = []
        self._embedder: Embedder | None = None  # loaded in setup(); tests may inject one
        self._vectors: VectorIndex | None = None

    async def setup(self) -> None:
        db_path = (
            self.spec.config.get("path")
            or os.environ.get("SQLITE_PATH")
            or "/data/probable-intel/main.db"
        )
        # Subscribe before the awaited open() so packets published meanwhile are queued.
        self._subscriptions = [
            self.spine.subscribe(ch) for ch in self.spec.subscribe_channels
        ]
        self._backend = SQLiteBackend(Path(db_path))
        await self._backend.open()
        if self.spec.config.get("vector_index", True):
            await self._setup_vector_index()

    async def _setup_vector_index(self) -> None:
        if self._embedder is None:
            model = self.spec.config.get("embedding_model", DEFAULT_MODEL)
            # Model loading is slow and CPU-bound: keep it off the event loop.
            loop = asyncio.get_running_loop()
            self._embedder = await loop.run_in_executor(None, load_embedder, model)
        if self._embedder is None or self._backend is None or self._backend.connection is None:
            return
        self._vectors = VectorIndex(self._backend.connection, self._embedder.model_name)
        await self._vectors.ensure_schema()
        log.info("node %s: semantic index enabled (%s)", self.node_id, self._embedder.model_name)

    async def teardown(self) -> None:
        for sub in self._subscriptions:
            sub.close()
        if self._backend:
            await self._backend.close()

    async def run(self) -> None:
        packet = await self._wait_any(self._subscriptions)
        if packet is None:
            return
        if self._backend:
            await self._backend.save(packet)
            log.debug("node %s stored packet %s", self.node_id, packet.packet_id)
        if self._vectors is not None:
            await self._index(packet)

    async def _index(self, packet: IntelPacket) -> None:
        text = str(packet.payload.get("content") or "")
        if not text or self._vectors is None or self._embedder is None:
            return
        try:
            if await self._vectors.has(packet.source_hash):
                return  # same article from another pipeline stage
            loop = asyncio.get_running_loop()
            [vector] = await loop.run_in_executor(None, self._embedder.embed, [text])
            await self._vectors.add(packet.source_hash, vector)
        except Exception as e:  # indexing must never cost us the stored packet
            log.warning("node %s: semantic indexing failed for %s: %s",
                        self.node_id, packet.source_hash, e)
