from __future__ import annotations

import asyncio
import json
import logging
import time
from fnmatch import fnmatch
from typing import TYPE_CHECKING

from ..spine.packet import IntelPacket, Priority, TrustLevel

if TYPE_CHECKING:
    from ..nexus.spec import FederationSpec
    from ..spine.spine import Spine

log = logging.getLogger(__name__)


def _downgrade_trust(level: TrustLevel) -> TrustLevel:
    """Reduce trust by one tier on crossing a federation boundary."""
    values = list(TrustLevel)
    idx = values.index(level)
    return values[max(0, idx - 1)]


def _packet_to_dict(packet: IntelPacket) -> dict:
    return {
        "packet_id": str(packet.packet_id),
        "packet_type": packet.packet_type,
        "source_node_id": packet.source_node_id,
        "apparatus_id": packet.apparatus_id,
        "channel": packet.channel,
        "timestamp_utc": packet.timestamp_utc.isoformat(),
        "trust_level": packet.trust_level.name,
        "confidence": packet.confidence,
        "priority": packet.priority.name,
        "ttl_seconds": packet.ttl_seconds,
        "payload": packet.payload,
        "provenance": packet.provenance,
        "tags": packet.tags,
        "source_hash": packet.source_hash,
    }


def _dict_to_packet(data: dict) -> IntelPacket:
    from datetime import datetime, timezone
    from uuid import UUID
    return IntelPacket(
        packet_id=UUID(data["packet_id"]),
        packet_type=data["packet_type"],
        source_node_id=data["source_node_id"],
        apparatus_id=data.get("apparatus_id", "federated"),
        channel=data["channel"],
        timestamp_utc=datetime.fromisoformat(data["timestamp_utc"]).replace(tzinfo=timezone.utc),
        trust_level=TrustLevel[data.get("trust_level", "UNCLASSIFIED")],
        confidence=float(data.get("confidence", 0.5)),
        priority=Priority[data.get("priority", "NORMAL")],
        ttl_seconds=int(data.get("ttl_seconds", 300)),
        payload=data.get("payload", {}),
        provenance=data.get("provenance", []),
        tags=data.get("tags", []),
        source_hash=data.get("source_hash", ""),
    )


class FederatedSpine:
    """Bridges the local Spine to peer hubs over HTTP.

    Push mode: packets tagged 'federate' or CRITICAL (if auto_federate_critical)
    are POSTed to each peer's /federate/ingest endpoint.

    Pull mode: subscribes to peers' /federate/stream SSE endpoints and
    re-publishes incoming packets on the local Spine with trust downgraded.
    """

    def __init__(self, spine: "Spine", spec: "FederationSpec") -> None:
        self._spine = spine
        self._spec = spec
        self._outbound: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._tasks: list[asyncio.Task] = []
        self._peer_status: dict[str, str] = {p.url: "unknown" for p in spec.peers}
        # Fan-out queues for SSE stream subscribers
        self._stream_subs: list[asyncio.Queue] = []

    def start(self) -> None:
        self._tasks.append(asyncio.create_task(self._push_loop(), name="fed-push"))
        for peer in self._spec.peers:
            self._tasks.append(
                asyncio.create_task(self._pull_loop(peer), name=f"fed-pull-{peer.url}")
            )

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def enqueue(self, packet: IntelPacket) -> None:
        """Called by Hub when a packet should be considered for federation."""
        if "no-federate" in packet.tags:
            return
        should_push = (
            "federate" in packet.tags
            or (self._spec.auto_federate_critical and packet.priority == Priority.CRITICAL)
        )
        if should_push:
            try:
                self._outbound.put_nowait(packet)
            except asyncio.QueueFull:
                log.warning("federation outbound queue full; dropping packet %s", packet.packet_id)
            # Fan out to SSE subscribers
            for q in self._stream_subs:
                try:
                    q.put_nowait(packet)
                except asyncio.QueueFull:
                    pass

    def add_stream_subscriber(self) -> asyncio.Queue:
        """Add a new SSE stream subscriber; returns its queue."""
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._stream_subs.append(q)
        return q

    def remove_stream_subscriber(self, q: asyncio.Queue) -> None:
        if q in self._stream_subs:
            self._stream_subs.remove(q)

    async def ingest(self, data: dict, peer_url: str = "") -> None:
        """Receive a packet from a peer, downgrade trust, publish locally."""
        try:
            packet = _dict_to_packet(data)
        except Exception as e:
            log.warning("federation: failed to parse incoming packet from %s: %s", peer_url, e)
            return

        packet.trust_level = _downgrade_trust(packet.trust_level)
        packet.tags = list(packet.tags) + [f"federated", f"peer:{peer_url[:32]}"]

        # Only publish to configured ingest channels (glob matching)
        ingest_channels = self._spec.ingest_channels
        if ingest_channels:
            if not any(fnmatch(packet.channel, pat) for pat in ingest_channels):
                log.debug("federation: dropping %s (not in ingest_channels)", packet.channel)
                return

        await self._spine.publish(packet.channel, packet)
        log.debug("federation: ingested packet %s from peer %s", packet.packet_id, peer_url)

    # ── push loop ─────────────────────────────────────────────────────────────

    async def _push_loop(self) -> None:
        try:
            import httpx
        except ImportError:
            log.warning("federation push disabled: httpx not installed")
            return

        while True:
            try:
                packet = await self._outbound.get()
                data = _packet_to_dict(packet)
                for peer in self._spec.peers:
                    if not self._should_push_to_peer(packet, peer):
                        continue
                    await self._push_to_peer(httpx, peer, data)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("federation push error: %s", e)

    def _should_push_to_peer(self, packet: IntelPacket, peer) -> bool:
        if not peer.push_channels:
            return True
        return any(fnmatch(packet.channel, pat) for pat in peer.push_channels)

    async def _push_to_peer(self, httpx, peer, data: dict) -> None:
        from ..hub.secrets import SecretManager
        headers = {"Content-Type": "application/json"}
        if peer.api_key_env:
            key = SecretManager().get(peer.api_key_env, default="")
            if key:
                headers["X-Federation-Key"] = key

        url = peer.url.rstrip("/") + "/federate/ingest"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=data, headers=headers)
                if resp.status_code == 200:
                    self._peer_status[peer.url] = "ok"
                else:
                    log.warning("federation: peer %s rejected packet (HTTP %d)", peer.url, resp.status_code)
                    self._peer_status[peer.url] = f"error-{resp.status_code}"
        except Exception as e:
            log.warning("federation: push to %s failed: %s", peer.url, e)
            self._peer_status[peer.url] = "unreachable"

    # ── pull loop ─────────────────────────────────────────────────────────────

    async def _pull_loop(self, peer) -> None:
        try:
            import httpx
        except ImportError:
            return

        from ..hub.secrets import SecretManager
        headers = {}
        if peer.api_key_env:
            key = SecretManager().get(peer.api_key_env, default="")
            if key:
                headers["X-Federation-Key"] = key

        url = peer.url.rstrip("/") + "/federate/stream"
        backoff = 5.0
        while True:
            try:
                async with httpx.AsyncClient(timeout=None) as client:
                    async with client.stream("GET", url, headers=headers) as resp:
                        if resp.status_code != 200:
                            log.warning("federation: stream from %s returned %d", peer.url, resp.status_code)
                            await asyncio.sleep(backoff)
                            continue
                        self._peer_status[peer.url] = "streaming"
                        backoff = 5.0
                        async for line in resp.aiter_lines():
                            if line.startswith("data:"):
                                raw = line[5:].strip()
                                if raw:
                                    try:
                                        await self.ingest(json.loads(raw), peer_url=peer.url)
                                    except Exception as e:
                                        log.debug("federation: bad SSE line: %s", e)
            except asyncio.CancelledError:
                break
            except Exception as e:
                log.warning("federation: pull from %s failed: %s", peer.url, e)
                self._peer_status[peer.url] = "unreachable"
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 120.0)

    def peer_status(self) -> dict[str, str]:
        return dict(self._peer_status)
