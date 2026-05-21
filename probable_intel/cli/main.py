"""probable-intel CLI — the `pi` command."""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import warnings
from pathlib import Path

import typer

from ..nexus.errors import NEXUSError, NEXUSWarning

app = typer.Typer(
    name="pi",
    help="probable-intel: autonomous intelligence node network",
    no_args_is_help=True,
)


def _setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=getattr(logging, level.upper(), logging.INFO),
        stream=sys.stderr,
    )


@app.command()
def validate(
    apparatus: Path = typer.Argument(..., help="Path to .nx apparatus file"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Validate a NEXUS apparatus file without running it."""
    _setup_logging("DEBUG" if verbose else "WARNING")
    from ..nexus.loader import NexusLoader

    loader = NexusLoader()
    caught_warnings: list[str] = []

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always", NEXUSWarning)
        try:
            spec = loader.load(apparatus)
        except NEXUSError as e:
            typer.echo(f"[ERROR] {e}", err=True)
            raise typer.Exit(1)
        caught_warnings = [str(x.message) for x in w]

    typer.echo(f"[OK] apparatus: {spec.name!r} ({len(spec.nodes)} node(s))")
    typer.echo(f"     trust_level: {spec.trust_level}")
    typer.echo(f"     nodes:")
    for node in spec.nodes:
        emit_info = f" → {node.emits_channel}" if node.emits_channel else ""
        sub_info = f" ← {', '.join(node.subscribe_channels)}" if node.subscribe_channels else ""
        typer.echo(f"       [{node.node_type}] {node.node_id}{sub_info}{emit_info}")

    if caught_warnings:
        typer.echo("\n[WARNINGS]")
        for w_msg in caught_warnings:
            typer.echo(f"  ⚠ {w_msg}")
    else:
        typer.echo("\n  No warnings.")


@app.command()
def run(
    apparatus: Path = typer.Argument(..., help="Path to .nx apparatus file"),
    log_level: str = typer.Option("INFO", "--log-level", "-l"),
    api_port: int = typer.Option(0, "--api-port", help="Hub API port (0=random)"),
) -> None:
    """Load and run a NEXUS apparatus."""
    _setup_logging(log_level)
    from ..hub.hub import Hub

    hub = Hub()
    try:
        hub.load_apparatus(apparatus)
    except NEXUSError as e:
        typer.echo(f"[ERROR] {e}", err=True)
        raise typer.Exit(1)

    typer.echo(f"[pi] starting apparatus — {len(hub._registry.all_ids())} node(s)")

    async def _run() -> None:
        if api_port != 0:
            import uvicorn
            from ..hub.api import create_api

            api_app = create_api(hub)
            config = uvicorn.Config(api_app, host="127.0.0.1", port=api_port, log_level="warning")
            server = uvicorn.Server(config)
            await asyncio.gather(hub.run(), server.serve())
        else:
            await hub.run()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        typer.echo("\n[pi] shutting down")


@app.command()
def status(
    apparatus: Path = typer.Argument(..., help="Path to .nx apparatus file"),
) -> None:
    """Show status of a loaded apparatus (dry-run: parse only)."""
    _setup_logging("WARNING")
    from ..nexus.loader import NexusLoader

    try:
        spec = NexusLoader().load(apparatus)
    except NEXUSError as e:
        typer.echo(f"[ERROR] {e}", err=True)
        raise typer.Exit(1)

    typer.echo(json.dumps({
        "apparatus": spec.name,
        "version": spec.version,
        "trust_level": spec.trust_level,
        "node_count": len(spec.nodes),
        "nodes": [
            {
                "id": n.node_id,
                "type": n.node_type,
                "subscribes": n.subscribe_channels,
                "emits": n.emits_channel,
            }
            for n in spec.nodes
        ],
    }, indent=2))


_PRIORITY_COLORS = {
    "LOW": "\033[90m",       # dark grey
    "NORMAL": "\033[36m",    # cyan
    "HIGH": "\033[33m",      # yellow
    "CRITICAL": "\033[91m",  # bright red
}
_RESET = "\033[0m"


@app.command()
def watch(
    apparatus: Path = typer.Argument(..., help="Path to .nx apparatus file"),
    channel: str = typer.Option("", "--channel", "-c", help="Specific channel to watch (default: all)"),
    log_level: str = typer.Option("WARNING", "--log-level", "-l"),
    raw: bool = typer.Option(False, "--raw", help="Print raw JSON (no color)"),
) -> None:
    """Watch IntelPackets flow through the Spine in real-time.

    Loads the apparatus, starts all nodes, then prints every packet that
    flows through the specified channel (or all channels if none given).
    """
    _setup_logging(log_level)
    from ..hub.hub import Hub

    hub = Hub()
    try:
        spec = hub.load_apparatus(apparatus)
    except Exception as e:
        typer.echo(f"[ERROR] {e}", err=True)
        raise typer.Exit(1)

    # Determine channels to watch
    watch_channels = [channel] if channel else list(spec.emitting_channels())
    if not watch_channels:
        typer.echo("[WARN] no emitting channels found in apparatus", err=True)
        raise typer.Exit(1)

    typer.echo(
        f"[pi watch] apparatus={spec.name!r}  watching {len(watch_channels)} channel(s)",
        err=True,
    )
    for ch in watch_channels:
        typer.echo(f"  → {ch}", err=True)
    typer.echo("", err=True)

    async def _watch() -> None:
        subscriptions = [(ch, hub.spine.subscribe(ch)) for ch in watch_channels]
        hub_task = asyncio.create_task(hub.run())

        async def _drain(ch: str, sub: object) -> None:
            async for packet in sub:  # type: ignore[union-attr]
                _print_packet(ch, packet, raw)

        drain_tasks = [asyncio.create_task(_drain(ch, sub)) for ch, sub in subscriptions]

        try:
            await asyncio.gather(*drain_tasks)
        except asyncio.CancelledError:
            pass
        finally:
            hub_task.cancel()
            await asyncio.gather(hub_task, return_exceptions=True)

    try:
        asyncio.run(_watch())
    except KeyboardInterrupt:
        typer.echo("\n[pi watch] stopped", err=True)


def _print_packet(channel: str, packet: object, raw: bool) -> None:
    from ..spine.packet import IntelPacket
    p: IntelPacket = packet  # type: ignore[assignment]

    data = {
        "channel": channel,
        "packet_id": str(p.packet_id)[:8],
        "type": p.packet_type,
        "from": p.provenance[:2],
        "priority": p.priority.name,
        "confidence": round(p.confidence, 2),
        "ts": p.timestamp_utc.strftime("%H:%M:%S"),
        "payload_keys": list(p.payload.keys()),
    }

    if raw:
        typer.echo(json.dumps(data))
        return

    color = _PRIORITY_COLORS.get(p.priority.name, "")
    line = (
        f"{color}[{data['ts']}] "
        f"[{p.priority.name:8s}] "
        f"{p.packet_type:24s} "
        f"#{data['packet_id']}  "
        f"{' → '.join(data['from'])}"
        f"{_RESET}"
    )
    typer.echo(line)


if __name__ == "__main__":
    app()
