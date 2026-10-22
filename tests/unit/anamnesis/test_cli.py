from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from probable_intel.cli.main import app

runner = CliRunner()


def run(*args: str, input: str | None = None):
    return runner.invoke(app, list(args), input=input)


def test_end_to_end_capture_episode_lint_search(tmp_path: Path):
    root = tmp_path / "v"
    assert run("brain", "init", str(root)).exit_code == 0
    v = ["brain", "--vault", str(root)]

    r = run(*v, "capture", "-", "--tier", "T1", "--title", "Paper", "--json",
            input="Hippocampal indexing theory and retrieval.\n")
    assert r.exit_code == 0, r.output
    ref = json.loads(r.output)[0]["ref"]

    r = run(*v, "episodes", input=json.dumps(
        [{"proposition": "Paper proposes hippocampal indexing.", "source": ref}]))
    assert r.exit_code == 0, r.output

    r = run(*v, "episodes", input='{"proposition": "", "source": "session"}')
    assert r.exit_code == 2 and "nothing written" in r.output

    r = run(*v, "status", "--json")
    assert json.loads(r.output)["counts"]["inbox"] == 1

    assert run(*v, "mark", ref, "distilled").exit_code == 0
    assert run(*v, "lint").exit_code == 0

    r = run(*v, "search", "hippocampal", "--json")
    assert json.loads(r.output)[0]["ref"] == ref

    (root / "wiki" / "concepts" / "bad.md").write_text("---\nid: x\n---\n", encoding="utf-8")
    r = run(*v, "lint", "--json")
    assert r.exit_code == 1 and json.loads(r.output)["errors"] >= 1


def test_band_surprise_and_missing_vault(tmp_path: Path):
    assert run("brain", "band", "0.7").output.strip() == "likely"
    assert run("brain", "band", "2").exit_code == 2
    assert float(run("brain", "surprise", "0.5", "0.9").output) > 0.5
    r = run("brain", "--vault", str(tmp_path), "status")
    assert r.exit_code == 2 and "pi brain init" in r.output
