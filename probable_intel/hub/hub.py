from __future__ import annotations

import asyncio
import logging
import time
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
        self._federation = None
        self._active_directives: dict[str, dict] = {}
        self._background_tasks: list[asyncio.Task] = []

    def load_apparatus(self, path: Path | str) -> ApparatusSpec:
        spec = self._loader.load(Path(path))
        self._apparatuses.append(spec)
        for node_spec in spec.nodes:
            node = self._factory.create(node_spec, self.spine)
            self._registry.register(node)

        # Wire KnowledgeGraphNode references into AttributionNodes
        self._wire_kg_references()

        # Start federation if configured
        if spec.federation.enabled and not self._federation:
            from .federation import FederatedSpine
            self._federation = FederatedSpine(self.spine, spec.federation)

        log.info(
            "apparatus %r loaded: %d node(s) registered",
            spec.name,
            len(spec.nodes),
        )
        return spec

    def _wire_kg_references(self) -> None:
        kg_nodes = {
            n.node_id: n
            for n in self._registry.all_nodes()
            if n.__class__.__name__ == "KnowledgeGraphNode"
        }
        for node in self._registry.all_nodes():
            if node.__class__.__name__ == "AttributionNode":
                cfg = node.spec.config
                kg_id = cfg.get("kg_node_id", "")
                if kg_id and kg_id in kg_nodes:
                    node._kg_node = kg_nodes[kg_id]  # type: ignore[attr-defined]
                    log.debug("wired %s._kg_node → %s", node.node_id, kg_id)

    async def run(self) -> None:
        log.info("hub starting %d node(s)", len(self._registry.all_ids()))
        self._health.start()
        await self._lifecycle.start_all()

        if self._federation is not None:
            self._federation.start()
            log.info("hub: federation started (%d peers)", len(self._federation._spec.peers))

        self._background_tasks.append(
            asyncio.create_task(self._directive_loop(), name="hub-directives")
        )

        log.info("hub ready — all nodes running")
        try:
            await asyncio.Event().wait()  # run until cancelled
        except asyncio.CancelledError:
            pass
        finally:
            await self._shutdown()

    async def _directive_loop(self) -> None:
        """Apply TaskDirectivePackets from system.task.directives channel."""
        sub = self.spine.subscribe("system.task.directives")
        try:
            while True:
                packet = await sub.get()
                await self._apply_directive(packet)
                self._prune_directives()
        except asyncio.CancelledError:
            pass
        finally:
            sub.close()

    def _prune_directives(self) -> None:
        """Remove directives whose TTL has elapsed."""
        now = time.time()
        self._active_directives = {
            k: v for k, v in self._active_directives.items()
            if now - v.get("_applied_at", now) < v.get("ttl_seconds", 3600)
        }

    async def _apply_directive(self, packet) -> None:
        payload = packet.payload
        dtype = str(payload.get("directive_type", ""))
        target_id = str(payload.get("target_node_id", ""))
        params = payload.get("parameters", {})
        ttl = int(payload.get("ttl_seconds", 3600))

        log.info("hub: applying directive %r → %r (TTL %ds)", dtype, target_id, ttl)
        self._active_directives[str(packet.packet_id)] = {
            **payload, "_applied_at": time.time(),
        }

        node = self._registry.get(target_id)
        if node is None:
            log.debug("hub: directive target %r not found (may use wildcard)", target_id)
            return

        try:
            if dtype == "add_keyword_filter" and hasattr(node, "_keywords"):
                kw = str(params.get("keyword", ""))
                if kw:
                    node._keywords.add(kw)  # type: ignore[attr-defined]
                    log.info("hub: added keyword filter %r to %s", kw, target_id)

            elif dtype == "expand_collection":
                # Increase polling frequency
                for attr in ("_interval_seconds", "_interval"):
                    if hasattr(node, attr):
                        cur = getattr(node, attr)
                        setattr(node, attr, max(30, int(cur * 0.5)))
                        log.info("hub: expanded collection on %s → interval %ds", target_id, max(30, int(cur * 0.5)))
                        break
                # Inject a new URL target if provided
                new_url = str(params.get("url", ""))
                if new_url and hasattr(node, "_feed_urls"):
                    if new_url not in node._feed_urls:  # type: ignore[attr-defined]
                        node._feed_urls.append(new_url)  # type: ignore[attr-defined]
                        log.info("hub: injected feed URL into %s: %s", target_id, new_url)

            elif dtype == "pause_channel":
                node._paused_until = time.time() + ttl
                log.info("hub: paused %s for %ds", target_id, ttl)

            elif dtype == "deprioritize" and hasattr(node, "_emit_priority"):
                from ..spine.packet import Priority
                current = node._emit_priority  # type: ignore[attr-defined]
                if current.value > Priority.LOW.value:
                    node._emit_priority = Priority(current.value - 1)  # type: ignore[attr-defined]
                    log.info("hub: deprioritized %s → %s", target_id, node._emit_priority.name)

        except Exception as e:
            log.warning("hub: directive apply failed: %s", e)

    async def _shutdown(self) -> None:
        log.info("hub shutting down")
        for t in self._background_tasks:
            t.cancel()
        await asyncio.gather(*self._background_tasks, return_exceptions=True)
        if self._federation is not None:
            await self._federation.stop()
        await self._health.stop()
        await self._lifecycle.stop_all()
        log.info("hub stopped")

    def status(self) -> dict:
        return {
            "apparatuses": [a.name for a in self._apparatuses],
            "nodes": self._registry.snapshot(),
            "active_directives": len(self._active_directives),
            "federation": self._federation.peer_status() if self._federation else None,
        }
