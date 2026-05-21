from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

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

    return app


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
