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


if __name__ == "__main__":
    app()
