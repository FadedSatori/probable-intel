from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from ..base import BaseNode
from ...storage.sqlite_backend import SQLiteBackend

if TYPE_CHECKING:
    from ...nexus.spec import NodeSpec
    from ...spine.spine import Spine

log = logging.getLogger(__name__)


class StorageNode(BaseNode):
    """Persists IntelPackets from subscribed channels to a storage backend."""

    def __init__(self, spec: "NodeSpec", spine: "Spine") -> None:
        super().__init__(spec, spine)
        self._backend: SQLiteBackend | None = None

    async def setup(self) -> None:
        db_path = (
            self.spec.config.get("path")
            or os.environ.get("SQLITE_PATH")
            or "/data/probable-intel/main.db"
        )
        self._backend = SQLiteBackend(Path(db_path))
        await self._backend.open()
        self.subscribe()

    async def teardown(self) -> None:
        self.unsubscribe()
        if self._backend:
            await self._backend.close()

    async def run(self) -> None:
        packet = await self.receive()
        if self._backend:
            await self._backend.save(packet)
            log.debug("node %s stored packet %s", self.node_id, packet.packet_id)
