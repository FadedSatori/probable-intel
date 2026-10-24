"""Keep a live vault current (`pi brain upgrade`) and check it is wired up (`pi brain doctor`).

`init` only fills in missing files, so a vault created by an older release never
gets new safeguards. `upgrade` brings it forward without touching notes or the
owner's edits; `doctor` proves the hooks actually run, which is the failure that
is otherwise silent (hooks fail open when `pi` is not on Claude Code's PATH).
"""
from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from . import guard
from .notes import FrontmatterError, split_frontmatter
from .vault import TEMPLATE_DIR, Vault, template_files

HOOK_EVENTS: dict[str, str] = {  # Claude Code event → `pi brain hook` argument
    "PreToolUse": "pre-tool",
    "PostToolUse": "post-tool",
    "UserPromptSubmit": "prompt",
    "Stop": "stop",
}
# Files the package owns outright: replaced on upgrade (with a backup if they differ).
_OWNED_PREFIX = ".claude/skills/anamnesis-"


@dataclass(frozen=True)
class Action:
    path: str
    what: str


def _template_settings(hook_command: str) -> dict:
    data = json.loads((TEMPLATE_DIR / "_claude" / "settings.json").read_text(encoding="utf-8"))
    for entries in data["hooks"].values():
        for entry in entries:
            for h in entry["hooks"]:
                h["command"] = h["command"].replace("pi brain hook", f"{hook_command} brain hook", 1)
    return data


def _merge_settings(current: dict, wanted: dict) -> tuple[dict, list[str]]:
    """Ensure each Anamnesis hook is present; keep every hook the owner added."""
    added = []
    hooks = current.setdefault("hooks", {})
    for event, entries in wanted["hooks"].items():
        mine = entries[0]
        want_cmd = mine["hooks"][0]["command"]
        existing = hooks.setdefault(event, [])
        ours = [
            (i, e) for i, e in enumerate(existing)
            for h in e.get("hooks", []) if re.search(r"\bbrain hook\b", str(h.get("command", "")))
        ]
        if not ours:
            existing.append(mine)
            added.append(event)
        else:
            i, entry = ours[0]
            if any(h.get("command") != want_cmd for h in entry.get("hooks", [])
                   if re.search(r"\bbrain hook\b", str(h.get("command", "")))):
                existing[i] = mine
                added.append(f"{event} (command updated)")
    return current, added


def upgrade(vault: Vault, *, dry_run: bool = False, hook_command: str | None = None) -> list[Action]:
    """Bring a vault up to the current template. Never touches wiki/, raw/ or the owner's text.

    ``hook_command`` is the executable the hooks call. None keeps whatever the vault is
    already wired to (so an earlier `--absolute-pi` sticks), falling back to a bare `pi`.
    """
    actions: list[Action] = []
    if hook_command is None:
        current_cmds = _configured_commands(vault)
        hook_command = next(iter(current_cmds.values())).split()[0] if current_cmds else "pi"
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")

    def write(path: Path, text: str, what: str) -> None:
        actions.append(Action(path.relative_to(vault.root).as_posix(), what))
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    for rel, src in template_files().items():
        dst = vault.root / rel
        if rel == ".claude/settings.json":
            continue  # merged below
        if rel == "constitution.md" and dst.exists():
            continue  # owner's text: only the Seal section is ever appended (below)
        new = src.read_text(encoding="utf-8")
        if not dst.exists():
            write(dst, new, "added from template")
        elif rel.startswith(_OWNED_PREFIX) and dst.read_text(encoding="utf-8") != new:
            backup = dst.with_name(f"{dst.name}.bak-{stamp}")
            if not dry_run:
                shutil.copyfile(dst, backup)
            write(dst, new, f"updated (previous saved as {backup.name})")

    # .claude/settings.json: merge, never clobber the owner's own hooks.
    settings_path = vault.root / ".claude" / "settings.json"
    wanted = _template_settings(hook_command)
    try:
        current = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    except ValueError:
        current = None
    if current is None:
        actions.append(Action(".claude/settings.json", "NOT CHANGED: invalid JSON, fix it by hand"))
    else:
        merged, added = _merge_settings(current, wanted)
        if added:
            write(settings_path, json.dumps(merged, indent=2) + "\n", "hooks wired: " + ", ".join(added))

    # vault.yaml: add missing switches as appended lines, preserving the owner's comments.
    cfg_path = vault.root / "vault.yaml"
    if cfg_path.exists():
        try:
            have = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            have = None
        if have is None:
            actions.append(Action("vault.yaml", "NOT CHANGED: invalid YAML, fix it by hand"))
        else:
            missing = [k for k in guard.DEFAULTS if k not in have]
            if missing:
                lines = "".join(f"{k}: {json.dumps(guard.DEFAULTS[k])}\n" for k in missing)
                text = cfg_path.read_text(encoding="utf-8")
                write(cfg_path, text + ("" if text.endswith("\n") else "\n") + lines,
                      "added switches: " + ", ".join(missing))

    # constitution.md: append a Seal section if this vault predates it.
    if vault.constitution.exists() and not guard.seal_words(vault):
        text = vault.constitution.read_text(encoding="utf-8")
        numbers = [int(n) for n in re.findall(r"^##\s+(\d+)\.", text, re.MULTILINE)]
        n = max(numbers, default=0) + 1
        tmpl = (TEMPLATE_DIR / "constitution.md").read_text(encoding="utf-8")
        heading = guard.SEAL_HEADING.search(tmpl)
        if heading is None:
            raise RuntimeError("bundled constitution template has no Seal section")
        section = guard.SEAL_HEADING.sub(f"## {n}. Seal", tmpl[heading.start():], count=1)
        section = section.replace("{{SEAL_WORDS}}", guard.render_seal_list(guard.new_seal_words()))
        write(vault.constitution, text.rstrip("\n") + "\n\n" + section,
              f"appended §{n} Seal (seal probe word list)")

    # .gitignore: the search index and session state must never be committed.
    gi = vault.root / ".gitignore"
    if gi.exists() and ".anamnesis/" not in gi.read_text(encoding="utf-8").split():
        text = gi.read_text(encoding="utf-8")
        write(gi, text + ("" if text.endswith("\n") else "\n") + ".anamnesis/\n", "ignore .anamnesis/")

    if guard.canary_token(vault) is None:
        actions.append(Action(guard.CANARY_NOTE, "added honeytoken note"))
        if not dry_run:
            guard.ensure_canary(vault)

    for ledger in ("beliefs/ledger.jsonl", "retention/reviews.jsonl"):
        if not (vault.root / ledger).exists():
            actions.append(Action(ledger, "created"))
            if not dry_run:
                (vault.root / ledger).parent.mkdir(parents=True, exist_ok=True)
                (vault.root / ledger).touch()
    return actions


# ── doctor ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Check:
    status: str  # ok | warn | fail
    name: str
    detail: str


def _configured_commands(vault: Vault) -> dict[str, str]:
    path = vault.root / ".claude" / "settings.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for event, entries in (data.get("hooks") or {}).items():
        for entry in entries or []:
            for h in entry.get("hooks", []):
                cmd = str(h.get("command", ""))
                if re.search(r"\bbrain hook\b", cmd):
                    out[event] = cmd
    return out


def doctor(vault: Vault, *, run_hooks: bool = True) -> list[Check]:
    checks: list[Check] = []

    def add(status: str, name: str, detail: str) -> None:
        checks.append(Check(status, name, detail))

    for rel in ("constitution.md", "CLAUDE.md", "index.md", "log.md", "facets.yaml", "vault.yaml"):
        p = vault.root / rel
        add("ok" if p.exists() else "fail", f"file {rel}", "present" if p.exists() else "missing: run `pi brain upgrade`")

    claude_md = vault.root / "CLAUDE.md"
    if claude_md.exists() and "@constitution.md" not in claude_md.read_text(encoding="utf-8"):
        add("warn", "constitution import", "CLAUDE.md does not import @constitution.md, so it is not always in context")

    words = guard.seal_words(vault)
    add("ok" if len(words) >= 2 else "fail", "seal words",
        f"{len(words)} words in the constitution's Seal section" if words
        else "no Seal section: probes cannot work; run `pi brain upgrade`")

    add("ok" if guard.canary_token(vault) else "fail", "honeytoken",
        "canary note present" if guard.canary_token(vault) else "missing: run `pi brain upgrade`")

    cfg_path = vault.root / "vault.yaml"
    try:
        if cfg_path.exists():
            yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        add("warn", "switches", f"vault.yaml is invalid YAML, so defaults apply: {e}")
    else:
        cfg = guard.load_config(vault)
        off = [k for k in ("guards", "lint_on_write", "honeytoken") if not cfg[k]]
        if not cfg["seal_every"]:
            off.append("seal_every")
        add("warn" if off else "ok", "switches",
            "off: " + ", ".join(off) if off else "all safeguards on")

    for name in ("capture", "ingest", "query", "lint"):
        skill = vault.root / ".claude" / "skills" / f"anamnesis-{name}" / "SKILL.md"
        try:
            fm, _ = split_frontmatter(skill.read_text(encoding="utf-8"))
        except (OSError, FrontmatterError):
            fm = None
        if fm is None:
            add("fail", f"skill anamnesis-{name}", "missing or unparseable: run `pi brain upgrade`")
        elif fm.get("description"):
            add("ok", f"skill anamnesis-{name}", "loads with a description")
        else:
            add("fail", f"skill anamnesis-{name}", "frontmatter has no description: it will never trigger")

    commands = _configured_commands(vault)
    missing_events = [e for e in HOOK_EVENTS if e not in commands]
    add("fail" if missing_events else "ok", "hooks configured",
        "missing in .claude/settings.json: " + ", ".join(missing_events) + "; run `pi brain upgrade`"
        if missing_events else "all four hooks wired")

    if commands:
        exe = next(iter(commands.values())).split()[0]
        resolved = exe if Path(exe).is_absolute() else shutil.which(exe)
        if resolved and Path(resolved).exists():
            add("ok", "hook executable", f"{exe} → {resolved}")
        else:
            add("fail", "hook executable",
                f"`{exe}` is not on this PATH, so every hook silently does nothing. "
                "Run `pi brain upgrade --absolute-pi` to wire an absolute path.")
        if not Path(exe).is_absolute():
            add("warn", "hook PATH",
                "hooks call a bare `pi`. Claude Code launched from a desktop app may not have your "
                "shell's PATH; `pi brain upgrade --absolute-pi` removes that risk.")

        if run_hooks and "PreToolUse" in commands and (resolved and Path(resolved).exists()):
            add(*_self_test(vault, commands["PreToolUse"]))

    git = shutil.which("git")
    if git and (vault.root / ".git").exists():
        add("ok", "git", "vault is a Git repository")
    else:
        add("warn", "git", "not a Git repository: history is your 'what did I believe then' record")
    return checks


def _self_test(vault: Vault, command: str) -> tuple[str, str, str]:
    """Run the real PreToolUse hook command on a forbidden write and expect a denial.

    The command comes from the vault's settings.json, which a cloned vault could have
    rewritten, so it is never handed to a shell and must be exactly `<pi> brain hook ...`.
    """
    try:
        argv = shlex.split(command)
    except ValueError:
        argv = []
    if (len(argv) != 4 or Path(argv[0]).name != "pi" or argv[1:3] != ["brain", "hook"]
            or argv[3] not in HOOK_EVENTS.values()):
        return "fail", "hook self-test", f"refusing to run unexpected hook command: {command!r}"
    event = {"tool_name": "Write", "cwd": str(vault.root),
             "tool_input": {"file_path": str(vault.constitution), "content": "doctor self-test"}}
    try:
        proc = subprocess.run(argv, input=json.dumps(event), capture_output=True, check=False,
                              text=True, timeout=30, cwd=vault.root)
    except (OSError, subprocess.TimeoutExpired) as e:
        return "fail", "hook self-test", f"could not run `{command}`: {e}"
    try:
        out = json.loads(proc.stdout or "{}")
        decision = out["hookSpecificOutput"]["permissionDecision"]
    except (ValueError, KeyError, TypeError):
        detail = (proc.stderr or proc.stdout or "no output").strip()[:200]
        return "fail", "hook self-test", f"guard did not deny a write to constitution.md ({detail})"
    if decision == "deny":
        return "ok", "hook self-test", "real PreToolUse hook denied a write to constitution.md"
    return "fail", "hook self-test", f"guard answered {decision!r} instead of deny"
