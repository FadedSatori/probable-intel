"""`pi brain upgrade` and `pi brain doctor`."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from probable_intel.anamnesis import Vault
from probable_intel.anamnesis import guard as g
from probable_intel.anamnesis.maintain import doctor, upgrade
from probable_intel.cli.main import app

from .conftest import write_note


def make_old_vault(vault: Vault) -> None:
    """Strip a fresh vault back to what a Phase 0 (pre-tripwire) release produced."""
    text = vault.constitution.read_text(encoding="utf-8")
    vault.constitution.write_text(text[: text.index("## 9.")] + "## 9. My own section\n\nmine\n",
                                  encoding="utf-8")
    (vault.root / "vault.yaml").unlink()
    (vault.root / ".claude" / "settings.json").unlink()
    (vault.root / g.CANARY_NOTE).unlink()
    gi = vault.root / ".gitignore"
    gi.write_text(gi.read_text(encoding="utf-8").replace(".anamnesis/\n", ""), encoding="utf-8")


def test_upgrade_brings_an_old_vault_forward_without_touching_owner_text(vault: Vault):
    make_old_vault(vault)
    note = write_note(vault, "concepts/n.md", "id: n_n\ntype: concept\ntitle: N", "my note")
    before_note = note.read_text(encoding="utf-8")

    actions = upgrade(vault)
    done = {a.path for a in actions}
    assert {"vault.yaml", ".claude/settings.json", "constitution.md", ".gitignore", g.CANARY_NOTE} <= done

    text = vault.constitution.read_text(encoding="utf-8")
    assert "## 9. My own section\n\nmine" in text  # owner text kept
    assert re.search(r"^## 10\. Seal", text, re.MULTILINE)  # numbered after the owner's last section
    assert len(g.seal_words(vault)) == g.SEAL_COUNT
    assert g.canary_token(vault)
    assert ".anamnesis/" in (vault.root / ".gitignore").read_text(encoding="utf-8")
    assert note.read_text(encoding="utf-8") == before_note
    assert [c for c in doctor(vault, run_hooks=False) if c.status == "fail"] == []


def test_upgrade_is_idempotent_and_dry_run_changes_nothing(vault: Vault):
    make_old_vault(vault)
    snapshot = {p: p.read_bytes() for p in vault.root.rglob("*") if p.is_file()}
    assert upgrade(vault, dry_run=True)
    assert {p: p.read_bytes() for p in vault.root.rglob("*") if p.is_file()} == snapshot

    assert upgrade(vault)
    assert upgrade(vault) == []


def test_upgrade_merges_hooks_and_keeps_the_owners_own(vault: Vault):
    path = vault.root / ".claude" / "settings.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["hooks"]["PreToolUse"].append({"matcher": "Bash", "hooks": [{"type": "command", "command": "my-audit"}]})
    del data["hooks"]["Stop"]
    data["permissions"] = {"allow": ["Bash(ls:*)"]}
    path.write_text(json.dumps(data), encoding="utf-8")

    upgrade(vault)
    merged = json.loads(path.read_text(encoding="utf-8"))
    commands = [h["command"] for e in merged["hooks"]["PreToolUse"] for h in e["hooks"]]
    assert "my-audit" in commands and "pi brain hook pre-tool" in commands
    assert merged["hooks"]["Stop"][0]["hooks"][0]["command"] == "pi brain hook stop"
    assert merged["permissions"] == {"allow": ["Bash(ls:*)"]}


def test_upgrade_absolute_command_rewires_existing_hooks(vault: Vault):
    upgrade(vault, hook_command="/opt/tools/pi")
    cmds = {h["command"] for es in json.loads((vault.root / ".claude" / "settings.json")
            .read_text(encoding="utf-8"))["hooks"].values() for e in es for h in e["hooks"]}
    assert cmds == {f"/opt/tools/pi brain hook {e}" for e in ("pre-tool", "post-tool", "prompt", "stop")}
    assert upgrade(vault) == []  # a later plain upgrade keeps the absolute path (no flip-flop)


def test_upgrade_replaces_stale_skill_with_backup(vault: Vault):
    skill = vault.root / ".claude" / "skills" / "anamnesis-lint" / "SKILL.md"
    skill.write_text("---\nname: anamnesis-lint\ndescription: old\n---\nold body\n", encoding="utf-8")
    actions = upgrade(vault)
    assert any("anamnesis-lint" in a.path and "previous saved" in a.what for a in actions)
    assert "old body" not in skill.read_text(encoding="utf-8")
    backups = list(skill.parent.glob("SKILL.md.bak-*"))
    assert len(backups) == 1 and "old body" in backups[0].read_text(encoding="utf-8")


def test_upgrade_adds_missing_switches_and_keeps_owner_values(vault: Vault):
    cfg = vault.root / "vault.yaml"
    cfg.write_text("# mine\nseal_every: 9\n", encoding="utf-8")
    upgrade(vault)
    text = cfg.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert text.startswith("# mine\nseal_every: 9\n") and data["seal_every"] == 9
    assert set(g.DEFAULTS) <= set(data)


def test_upgrade_leaves_invalid_files_alone(vault: Vault):
    path = vault.root / ".claude" / "settings.json"
    path.write_text("{broken", encoding="utf-8")
    actions = upgrade(vault)
    assert any("invalid JSON" in a.what for a in actions)
    assert path.read_text(encoding="utf-8") == "{broken"


def test_doctor_flags_what_breaks_silently(vault: Vault):
    make_old_vault(vault)
    status = {c.name: c.status for c in doctor(vault, run_hooks=False)}
    assert status["seal words"] == "fail"
    assert status["hooks configured"] == "fail"
    assert status["honeytoken"] == "fail"
    assert status["file vault.yaml"] == "fail"

    skill = vault.root / ".claude" / "skills" / "anamnesis-query" / "SKILL.md"
    skill.write_text("---\nname: x\ndescription: broken: yaml: here\n---\n", encoding="utf-8")
    assert {c.name: c.status for c in doctor(vault, run_hooks=False)}["skill anamnesis-query"] == "fail"


def test_doctor_reports_missing_executable(vault: Vault):
    upgrade(vault, hook_command="/nonexistent/bin/pi")
    checks = {c.name: c for c in doctor(vault)}
    assert checks["hook executable"].status == "fail"
    assert "hook self-test" not in checks  # never runs a command that does not exist


@pytest.mark.parametrize("command", [
    "pi brain hook pre-tool; touch PWNED",
    "sh -c 'touch PWNED'",
    "pi brain hook pre-tool extra",
])
def test_doctor_self_test_refuses_unexpected_commands(vault: Vault, command: str, tmp_path: Path):
    from probable_intel.anamnesis.maintain import _self_test

    status, _, detail = _self_test(vault, command)
    assert status == "fail" and "refusing" in detail
    assert not (vault.root / "PWNED").exists()


def test_cli_upgrade_and_doctor(vault: Vault):
    runner = CliRunner()
    make_old_vault(vault)
    r = runner.invoke(app, ["brain", "--vault", str(vault.root), "upgrade", "--dry-run"])
    assert r.exit_code == 0 and "[dry-run]" in r.output
    r = runner.invoke(app, ["brain", "--vault", str(vault.root), "upgrade"])
    assert r.exit_code == 0 and "change(s)" in r.output
    r = runner.invoke(app, ["brain", "--vault", str(vault.root), "upgrade"])
    assert "already up to date" in r.output
    r = runner.invoke(app, ["brain", "--vault", str(vault.root), "doctor", "--no-run", "--json"])
    rows = json.loads(r.output)
    assert not [x for x in rows if x["status"] == "fail" and x["name"] != "hook executable"]
