from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

if TYPE_CHECKING:
    from .hub import Hub

log = logging.getLogger(__name__)


def create_api(hub: "Hub") -> FastAPI:
    app = FastAPI(title="probable-intel hub", docs_url=None, redoc_url=None)

    # ── real endpoints ──────────────────────────────────────────────────────

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "timestamp": time.time()}

    @app.get("/status")
    async def status() -> dict:
        return hub.status()

    @app.post("/node/{node_id}/stop")
    async def stop_node(node_id: str) -> dict:
        node = hub._registry.get(node_id)
        if not node:
            return JSONResponse({"error": "not found"}, status_code=404)
        await hub._lifecycle.stop(node_id)
        return {"stopped": node_id}

    @app.post("/node/{node_id}/restart")
    async def restart_node(node_id: str) -> dict:
        node = hub._registry.get(node_id)
        if not node:
            return JSONResponse({"error": "not found"}, status_code=404)
        await hub._lifecycle.restart(node_id)
        return {"restarted": node_id}

    # ── honeypot endpoints (deception layer) ──────────────────────────────

    @app.get("/api/v1/nodes/list")
    async def honeypot_nodes_list(request: Request) -> JSONResponse:
        await _fire_canary(hub, "honeypot-api-nodes", request)
        return JSONResponse({
            "nodes": [
                {"id": "node-alpha-1", "status": "running", "type": "WebNode"},
                {"id": "node-beta-3", "status": "running", "type": "FeedNode"},
                {"id": "node-gamma-7", "status": "idle", "type": "ApiNode"},
            ]
        })

    @app.get("/api/v1/tasks/pending")
    async def honeypot_tasks(request: Request) -> JSONResponse:
        await _fire_canary(hub, "honeypot-api-tasks", request)
        return JSONResponse({
            "pending": [
                {"task_id": "t-001", "type": "scrape", "priority": "high"},
                {"task_id": "t-002", "type": "analyze", "priority": "normal"},
            ]
        })

    @app.get("/admin/status")
    async def honeypot_admin(request: Request) -> Response:
        await _fire_canary(hub, "honeypot-dash-01", request)
        html = (
            "<html><body><h1>Operational Dashboard</h1>"
            "<p>All systems nominal. 12 nodes active.</p></body></html>"
        )
        return Response(content=html, media_type="text/html")

    @app.get("/beacon/{token}")
    async def canary_beacon(token: str, request: Request) -> Response:
        canary_id = f"canary:{token}"
        await _fire_canary(hub, canary_id, request)
        # Return transparent 1x1 GIF
        gif = (
            b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00"
            b"!\xf9\x04\x00\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01"
            b"\x00\x00\x02\x02D\x01\x00;"
        )
        return Response(content=gif, media_type="image/gif")

    # ── federation endpoints ────────────────────────────────────────────────

    @app.post("/federate/ingest")
    async def federate_ingest(
        request: Request,
        x_federation_key: str | None = Header(default=None),
    ) -> dict:
        _check_federation_key(hub, x_federation_key)
        data = await request.json()
        fed = getattr(hub, "_federation", None)
        if fed is None:
            raise HTTPException(status_code=503, detail="federation not enabled")
        await fed.ingest(data, peer_url=str(request.client.host if request.client else "unknown"))
        return {"accepted": True}

    @app.get("/federate/stream")
    async def federate_stream(
        x_federation_key: str | None = Header(default=None),
    ) -> StreamingResponse:
        _check_federation_key(hub, x_federation_key)
        fed = getattr(hub, "_federation", None)
        if fed is None:
            raise HTTPException(status_code=503, detail="federation not enabled")

        import asyncio

        async def event_stream():
            from .federation import _packet_to_dict
            q = fed.add_stream_subscriber()
            try:
                while True:
                    try:
                        packet = await asyncio.wait_for(q.get(), timeout=30.0)
                        data = json.dumps(_packet_to_dict(packet))
                        yield f"data:{data}\n\n"
                    except asyncio.TimeoutError:
                        yield ": heartbeat\n\n"
            finally:
                fed.remove_stream_subscriber(q)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.get("/federate/peers")
    async def federate_peers(
        x_federation_key: str | None = Header(default=None),
    ) -> dict:
        _check_federation_key(hub, x_federation_key)
        fed = getattr(hub, "_federation", None)
        if fed is None:
            return {"peers": [], "enabled": False}
        return {"peers": fed.peer_status(), "enabled": True}

    return app


def _check_federation_key(hub, provided: str | None) -> None:
    """Validate X-Federation-Key against configured secret."""
    from ..hub.secrets import SecretManager
    expected = SecretManager().get("FEDERATION_API_KEY", default="")
    if expected and provided != expected:
        raise HTTPException(status_code=401, detail="invalid federation key")


async def _fire_canary(hub: "Hub", canary_id: str, request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    log.warning("canary fired: %s from %s %s", canary_id, ip, request.url.path)

    for node in hub._registry.all_nodes():
        if node.__class__.__name__ == "DeceptionNode":
            await node.trigger(  # type: ignore[attr-defined]
                canary_id=canary_id,
                requestor_ip=ip,
                method=request.method,
                path=str(request.url.path),
                headers=dict(request.headers),
            )
            break
