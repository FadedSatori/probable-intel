from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from ..nexus.loader import NexusLoader
from ..nexus.spec import ApparatusSpec
from ..spine.spine import Spine
from .factory import NodeFactory
from .health import HealthMonitor
from .lifecycle import NodeLifecycleManager
from .registry import NodeRegistry

log = logging.getLogger(__name__)


class Hub:
    """Orchestrates node lifecycle for one or more NEXUS apparatus definitions."""

    def __init__(self) -> None:
        self.spine = Spine()
        self._registry = NodeRegistry()
        self._lifecycle = NodeLifecycleManager(self._registry)
        self._health = HealthMonitor(self._registry, self._lifecycle)
        self._factory = NodeFactory()
        self._loader = NexusLoader()
        self._apparatuses: list[ApparatusSpec] = []

    def load_apparatus(self, path: Path | str) -> ApparatusSpec:
        spec = self._loader.load(Path(path))
        self._apparatuses.append(spec)
        for node_spec in spec.nodes:
            node = self._factory.create(node_spec, self.spine)
            self._registry.register(node)
        log.info(
            "apparatus %r loaded: %d node(s) registered",
            spec.name,
            len(spec.nodes),
        )
        return spec

    async def run(self) -> None:
        log.info("hub starting %d node(s)", len(self._registry.all_ids()))
        self._health.start()
        await self._lifecycle.start_all()
        log.info("hub ready — all nodes running")
        try:
            await asyncio.Event().wait()  # run until cancelled
        except asyncio.CancelledError:
            pass
        finally:
            await self._shutdown()

    async def _shutdown(self) -> None:
        log.info("hub shutting down")
        await self._health.stop()
        await self._lifecycle.stop_all()
        log.info("hub stopped")

    def status(self) -> dict:
        return {
            "apparatuses": [a.name for a in self._apparatuses],
            "nodes": self._registry.snapshot(),
        }
