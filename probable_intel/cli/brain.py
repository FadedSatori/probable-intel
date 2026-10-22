"""`pi brain` — deterministic operations on an Anamnesis vault.

Claude Code skills call these for every mechanical step (hashing, logging,
validation, lint, search) so that the LLM only does judgment work.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from ..anamnesis import Vault, VaultNotFound
from ..anamnesis import beliefs as bel
from ..anamnesis.capture import (
    STATUSES,
    capture_bytes,
    capture_file,
    iter_raw,
    set_status,
    sweep_inbox,
)
from ..anamnesis.lint import lint as run_lint
from ..anamnesis.logbook import append_episodes, append_log, recent_log
from ..anamnesis.search import build_index
from ..anamnesis.search import search as run_search

brain_app = typer.Typer(
    name="brain",
    help="Anamnesis second brain: capture, log, lint, search a Markdown vault.",
    no_args_is_help=True,
)


@brain_app.callback()
def _root(
    ctx: typer.Context,
    vault: Path | None = typer.Option(
        None, "--vault", help="Vault root (default: nearest dir with constitution.md)"
    ),
) -> None:
    ctx.obj = {"vault": vault}


def _vault(ctx: typer.Context) -> Vault:
    start = (ctx.obj or {}).get("vault")
    try:
        return Vault.find(start)
    except VaultNotFound as e:
        typer.echo(f"[ERROR] {e}. Run `pi brain init <dir>` first.", err=True)
        raise typer.Exit(2)


def _out(data: object) -> None:
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2, default=str))


@brain_app.command()
def init(
    path: Path = typer.Argument(Path("."), help="Directory to create the vault in"),
    force: bool = typer.Option(False, "--force", help="Overwrite template files (skills, docs)"),
) -> None:
    """Create a vault (or fill in missing template files in an existing one)."""
    v = Vault.init(path, force=force)
    typer.echo(f"[OK] vault ready at {v.root}")
    typer.echo("     next: edit constitution.md (goals, facets), then open the folder in Claude Code")


@brain_app.command()
def capture(
    ctx: typer.Context,
    source: str | None = typer.Argument(None, help="File path, or '-' to read stdin"),
    title: str | None = typer.Option(None, "--title", "-t"),
    uri: str | None = typer.Option(None, "--uri", help="Where it came from"),
    channel: str = typer.Option("manual", "--channel", "-c"),
    tier: str = typer.Option("T3", "--tier", help="Provenance tier T0|T1|T2|T3|TQ"),
    ext: str | None = typer.Option(None, "--ext", help="Extension for stdin content (default .md)"),
    published: str | None = typer.Option(None, "--published", help="Publication date (valid time)"),
    sweep: bool = typer.Option(False, "--sweep", help="Capture everything in inbox/"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Store a source immutably in raw/ with a sidecar. Idempotent by content hash."""
    v = _vault(ctx)
    common = {"uri": uri, "channel": channel, "trust_tier": tier, "published_at": published}
    try:
        if sweep:
            results = sweep_inbox(v, **common)
        elif source is None:
            typer.echo("[ERROR] give a file path, '-' for stdin, or --sweep", err=True)
            raise typer.Exit(2)
        elif source == "-":
            data = sys.stdin.buffer.read()
            if not data.strip():
                typer.echo("[ERROR] stdin was empty", err=True)
                raise typer.Exit(2)
            results = [capture_bytes(v, data, title=title or "untitled", ext=ext or ".md", **common)]
        else:
            src = Path(source)
            if not src.is_file():
                typer.echo(f"[ERROR] no such file: {src}", err=True)
                raise typer.Exit(2)
            kw = dict(common, title=title or src.stem)
            if ext:
                kw["ext"] = ext
            results = [capture_file(v, src, **kw)]
    except ValueError as e:
        typer.echo(f"[ERROR] {e}", err=True)
        raise typer.Exit(2)

    rows = [
        {"ref": it.ref, "path": v.rel(it.path), "created": created, "tier": it.meta.get("trust_tier")}
        for it, created in results
    ]
    for it, created in results:
        if created:
            append_log(v, "capture", str(it.meta.get("title")), f"{it.ref} · {v.rel(it.path)}")
    if as_json:
        _out(rows)
        return
    for r in rows:
        state = "captured" if r["created"] else "already present"
        typer.echo(f"[OK] {state}: {r['ref']}  {r['path']}  ({r['tier']})")


@brain_app.command()
def status(ctx: typer.Context, as_json: bool = typer.Option(False, "--json")) -> None:
    """Inbox (undistilled raw items) and the most recent log entries."""
    v = _vault(ctx)
    items = iter_raw(v)
    inbox = [
        {"ref": it.ref, "title": it.meta.get("title"), "tier": it.meta.get("trust_tier"),
         "captured_at": it.meta.get("captured_at"), "path": v.rel(it.path)}
        for it in items if it.meta.get("status", "inbox") == "inbox"
    ]
    counts = {s: sum(1 for it in items if it.meta.get("status", "inbox") == s) for s in STATUSES}
    log = [{"date": d, "op": op, "title": t} for d, op, t in recent_log(v, 10)]
    if as_json:
        _out({"counts": counts, "inbox": inbox, "recent_log": log})
        return
    typer.echo("raw: " + ", ".join(f"{k}={n}" for k, n in counts.items()))
    for row in inbox:
        typer.echo(f"  inbox  {row['ref']}  [{row['tier']}]  {row['title']}")
    if log:
        typer.echo("recent log:")
        for row in log:
            typer.echo(f"  [{row['date']}] {row['op']} | {row['title']}")


@brain_app.command()
def mark(
    ctx: typer.Context,
    ref: str = typer.Argument(..., help="raw:<sha-prefix>"),
    new_status: str = typer.Argument(..., metavar="STATUS", help="distilled|archived|expired|inbox"),
) -> None:
    """Set a raw item's lifecycle status."""
    v = _vault(ctx)
    try:
        it = set_status(v, ref, new_status)
    except (KeyError, ValueError) as e:
        typer.echo(f"[ERROR] {e}", err=True)
        raise typer.Exit(2)
    typer.echo(f"[OK] {it.ref} → {new_status}")


@brain_app.command()
def episodes(
    ctx: typer.Context,
    file: Path | None = typer.Option(None, "--file", "-f", help="JSON array or JSONL; default stdin"),
) -> None:
    """Validate and append event propositions to episodic/YYYY-MM.jsonl (all-or-nothing)."""
    v = _vault(ctx)
    text = file.read_text(encoding="utf-8") if file else sys.stdin.read()
    text = text.strip()
    try:
        events = json.loads(text) if text.startswith("[") else [
            json.loads(line) for line in text.splitlines() if line.strip()
        ]
        written = append_episodes(v, events)
    except (json.JSONDecodeError, ValueError) as e:
        typer.echo(f"[ERROR] {e}", err=True)
        raise typer.Exit(2)
    typer.echo(f"[OK] appended {len(written)} episode(s): " + ", ".join(r["id"] for r in written))


@brain_app.command()
def log(
    ctx: typer.Context,
    op: str = typer.Argument(..., help="capture|ingest|query|lint|review|consolidate|note|purge"),
    title: str = typer.Argument(...),
    details: str = typer.Option("", "--details", "-d"),
) -> None:
    """Append a `## [date] op | title` entry to log.md."""
    v = _vault(ctx)
    try:
        typer.echo(append_log(v, op, title, details))
    except ValueError as e:
        typer.echo(f"[ERROR] {e}", err=True)
        raise typer.Exit(2)


@brain_app.command()
def lint(
    ctx: typer.Context,
    as_json: bool = typer.Option(False, "--json"),
    inbox_days: int = typer.Option(14, "--inbox-days"),
) -> None:
    """Structural health check. Exit code 1 if any error-level finding."""
    v = _vault(ctx)
    findings = run_lint(v, inbox_max_age_days=inbox_days)
    errors = sum(f.severity == "error" for f in findings)
    if as_json:
        _out({"errors": errors, "warnings": len(findings) - errors,
              "findings": [f.as_dict() for f in findings]})
    else:
        for f in findings:
            typer.echo(f"{f.severity.upper():5s} {f.rule:16s} {f.path}: {f.message}")
        typer.echo(f"\n{errors} error(s), {len(findings) - errors} warning(s)")
    if errors:
        raise typer.Exit(1)


@brain_app.command()
def search(
    ctx: typer.Context,
    query: str = typer.Argument(...),
    limit: int = typer.Option(10, "--limit", "-n"),
    kind: str | None = typer.Option(None, "--kind", help="note|raw"),
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """BM25 search over notes and text sources (index rebuilds incrementally)."""
    v = _vault(ctx)
    hits = run_search(v, query, limit=limit, kind=kind)
    if as_json:
        _out([h.__dict__ for h in hits])
        return
    if not hits:
        typer.echo("no matches")
    for rank, h in enumerate(hits, start=1):
        typer.echo(
            f"{rank:2d}. [{h.kind}] {h.ref}  {h.title}  (bm25 {h.score:.3g})\n"
            f"    {h.path}\n    {h.snippet}"
        )


@brain_app.command()
def index(ctx: typer.Context, full: bool = typer.Option(False, "--full")) -> None:
    """(Re)build the disposable search index."""
    n = build_index(_vault(ctx), full=full)
    typer.echo(f"[OK] indexed {n} changed file(s)")


@brain_app.command()
def band(p: float = typer.Argument(..., help="Probability in [0, 1]")) -> None:
    """Print the ICD-203 likelihood band for p."""
    try:
        typer.echo(bel.band(p))
    except ValueError as e:
        typer.echo(f"[ERROR] {e}", err=True)
        raise typer.Exit(2)


@brain_app.command()
def surprise(prior: float, posterior: float) -> None:
    """Bayesian surprise (bits) of moving a belief from PRIOR to POSTERIOR."""
    try:
        typer.echo(f"{bel.surprise_bits(prior, posterior):.4f}")
    except ValueError as e:
        typer.echo(f"[ERROR] {e}", err=True)
        raise typer.Exit(2)


HOOK_EVENTS = ("pre-tool", "post-tool", "prompt", "stop")


@brain_app.command()
def hook(
    event_name: str = typer.Argument(..., metavar="EVENT", help="pre-tool|post-tool|prompt|stop"),
) -> None:
    """Claude Code hook entry point: reads the hook's JSON on stdin (see vault .claude/settings.json).

    Outside a vault, or on any internal error, it allows and stays silent: a bug in the
    guard must never wedge the session. The error goes to stderr for the debug log.
    """
    from ..anamnesis import guard

    if event_name not in HOOK_EVENTS:
        typer.echo(f"[ERROR] EVENT must be one of {HOOK_EVENTS}", err=True)
        raise typer.Exit(2)
    try:
        event = json.loads(sys.stdin.read() or "{}")
        try:
            v = Vault.find(Path(event["cwd"]) if event.get("cwd") else None)
        except VaultNotFound:
            return
        cfg = guard.load_config(v)
        if event_name == "pre-tool":
            reason = guard.pre_tool(v, event, cfg)
            if reason:
                _emit({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                              "permissionDecision": "deny",
                                              "permissionDecisionReason": f"[anamnesis] {reason}"}})
        elif event_name == "post-tool":
            note = guard.post_tool(v, event, cfg)
            if note:
                _emit({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                              "additionalContext": note}})
        elif event_name == "prompt":
            note = guard.on_prompt(v, event, cfg)
            if note:
                _emit({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                              "additionalContext": note}})
        else:
            msg = guard.on_stop(v, event, cfg)
            if msg:
                _emit({"systemMessage": msg})
    except Exception as e:  # noqa: BLE001 — fail open, loudly in the debug log
        typer.echo(f"anamnesis hook {event_name} error: {e!r}", err=True)


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))


@brain_app.command()
def seal(ctx: typer.Context, as_json: bool = typer.Option(False, "--json")) -> None:
    """Seal-probe results per session: is the constitution staying in focus?"""
    from ..anamnesis.guard import seal_report

    rows = seal_report(_vault(ctx))
    if as_json:
        _out(rows)
        return
    if not rows:
        typer.echo("no probes yet")
    for r in rows:
        typer.echo(f"{r['session'][:12]}  turns={r['turns']} probes={r['probes']} pass={r['passes']} "
                   f"miss={r['misses']} lookup={r['lookups']} codeword_miss={r['codeword_misses']}  "
                   f"recent: {' '.join(r['recent'])}")


@brain_app.command()
def upgrade(
    ctx: typer.Context,
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would change; change nothing"),
    absolute_pi: bool = typer.Option(
        False, "--absolute-pi", help="Wire hooks to this `pi`'s absolute path (fixes PATH issues)"
    ),
) -> None:
    """Bring an existing vault up to the current release without touching notes or your text."""
    import shutil as _shutil

    from ..anamnesis.maintain import upgrade as run_upgrade

    hook_cmd = None  # keep the vault's current hook executable
    if absolute_pi:
        found = _shutil.which("pi") or (sys.argv[0] if Path(sys.argv[0]).name == "pi" else None)
        if not found:
            typer.echo("[ERROR] cannot find `pi` on PATH to make absolute", err=True)
            raise typer.Exit(2)
        hook_cmd = str(Path(found).resolve())
    v = _vault(ctx)
    actions = run_upgrade(v, dry_run=dry_run, hook_command=hook_cmd)
    prefix = "[dry-run] " if dry_run else ""
    for a in actions:
        typer.echo(f"{prefix}{a.path}: {a.what}")
    typer.echo(f"\n{prefix}{len(actions)} change(s)" if actions else "already up to date")


@brain_app.command()
def doctor(
    ctx: typer.Context,
    as_json: bool = typer.Option(False, "--json"),
    no_run: bool = typer.Option(False, "--no-run", help="Skip running the real hook command"),
) -> None:
    """Check the vault is wired up: files, seal list, skills, hooks, and a live hook self-test."""
    from ..anamnesis.maintain import doctor as run_doctor

    checks = run_doctor(_vault(ctx), run_hooks=not no_run)
    failed = sum(c.status == "fail" for c in checks)
    if as_json:
        _out([c.__dict__ for c in checks])
    else:
        mark = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}
        for c in checks:
            typer.echo(f"{mark[c.status]} {c.name:20s} {c.detail}")
        typer.echo(f"\n{failed} failure(s)")
    if failed:
        raise typer.Exit(1)
