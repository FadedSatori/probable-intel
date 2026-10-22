from __future__ import annotations

from datetime import UTC, datetime, timedelta

from probable_intel.anamnesis import Vault
from probable_intel.anamnesis.capture import capture_bytes, set_status
from probable_intel.anamnesis.lint import lint
from probable_intel.anamnesis.notes import link_key, wikilinks
from probable_intel.anamnesis.search import build_index, search

from .conftest import link_from_index, write_note


def rules(findings, path_part=""):
    return {f.rule for f in findings if path_part in f.path}


def test_wikilink_parsing_ignores_code_and_strips_alias_heading():
    body = "See [[A note|alias]], ![[B#Heading]], [[c#^blk]].\n```\n[[not-a-link]]\n```\n`[[nor-this]]`"
    assert wikilinks(body) == ["A note", "B", "c"]
    assert link_key("Folder/My Note.md") == "my-note"


def test_fresh_vault_is_clean(vault: Vault):
    assert lint(vault) == []


def test_well_formed_linked_notes_are_clean(vault: Vault):
    item, _ = capture_bytes(vault, b"evidence", title="e", trust_tier="T2")
    write_note(vault, "concepts/a.md",
               f"id: n_a\ntype: concept\ntitle: Idea A\nsources: [{item.ref}]\n"
               "claims:\n  - {id: c1, text: t, p: 0.7, band: likely}",
               "Links to [[Idea B]].")
    write_note(vault, "concepts/b.md", "id: n_b\ntype: concept\ntitle: Idea B\naliases: [bee]",
               "Back to [[n_a]] and [[bee]].")
    link_from_index(vault, "n_a", "n_b")
    set_status(vault, item.ref, "distilled")
    assert lint(vault) == []


def test_structural_errors_are_reported(vault: Vault):
    write_note(vault, "concepts/nofm.md", "", "")
    (vault.wiki / "concepts" / "nofm.md").write_text("just text, no frontmatter\n", encoding="utf-8")
    write_note(vault, "concepts/bad.md", "id: n_bad\ntype: thought\n", "[[ghost]]")
    write_note(vault, "concepts/dup1.md", "id: n_dup\ntype: concept\ntitle: D1")
    write_note(vault, "concepts/dup2.md", "id: n_dup\ntype: concept\ntitle: D2")
    write_note(vault, "concepts/claims.md",
               "id: n_c\ntype: concept\ntitle: C\nsources: [raw:000000000000]\n"
               "claims:\n  - {id: c1, p: 0.8, band: very likely}\n  - {id: c2, p: 1.5}")
    write_note(vault, "concepts/unanchored.md",
               "id: n_u\ntype: concept\ntitle: U\nclaims:\n  - {id: c1, p: 0.5}")
    findings = lint(vault)
    assert "frontmatter" in rules(findings, "nofm.md")
    assert {"frontmatter", "broken-link"} <= rules(findings, "bad.md")
    assert "duplicate-id" in rules(findings)
    assert {"source-ref", "claim-band", "claim"} <= rules(findings, "claims.md")
    assert "unanchored-claim" in rules(findings, "unanchored.md")
    assert {"orphan", "not-in-index"} <= rules(findings, "dup1.md")


def test_stale_inbox_flagged_after_threshold(vault: Vault):
    capture_bytes(vault, b"old", title="old")
    assert "stale-inbox" not in rules(lint(vault))
    later = datetime.now(UTC) + timedelta(days=30)
    assert "stale-inbox" in rules(lint(vault, now=later))


def test_search_finds_notes_and_raw_and_updates_incrementally(vault: Vault):
    capture_bytes(vault, b"Personalized PageRank spreads activation for multi-hop recall.",
                  title="ppr paper", trust_tier="T1")
    path = write_note(vault, "concepts/ppr.md", "id: n_ppr\ntype: concept\ntitle: PPR recall",
                      "Graph walk from seed nodes.")
    hits = search(vault, "pagerank multi-hop")
    assert hits and hits[0].kind == "raw"
    assert [h.ref for h in search(vault, "seed nodes", kind="note")] == ["n_ppr"]

    path.write_text(path.read_text(encoding="utf-8").replace("seed nodes", "anchors"), encoding="utf-8")
    import os
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5))
    assert search(vault, "seed nodes", kind="note") == []
    assert [h.ref for h in search(vault, "anchors")] == ["n_ppr"]

    path.unlink()
    assert search(vault, "anchors") == []


def test_search_survives_punctuation_and_index_loss(vault: Vault):
    write_note(vault, "concepts/x.md", "id: n_x\ntype: concept\ntitle: X", "C++ and AND/OR (NEAR) \"quotes\"")
    assert search(vault, 'C++ "AND" (NEAR*') != []
    assert search(vault, "!!!") == []
    vault.index_db.unlink()
    assert build_index(vault) == 1


def test_facets_must_come_from_vocabulary(vault: Vault):
    write_note(vault, "concepts/f.md",
               "id: n_f\ntype: concept\ntitle: F\n"
               "facets: {domain: [memory-systems, agent-memory, retrieval], para: resource, mood: [x]}")
    link_from_index(vault, "n_f")
    msgs = [f.message for f in lint(vault) if f.rule.startswith("facet")]
    assert any("use prefLabel 'memory-systems' instead of 'agent-memory'" in m for m in msgs)
    assert any("'retrieval' is not in facets.yaml" in m for m in msgs)
    assert any("facet 'mood' is not in facets.yaml" in m for m in msgs)
    assert not any("'resource'" in m or "'memory-systems' is not" in m for m in msgs)


def test_facets_unchecked_without_vocabulary(vault: Vault):
    (vault.root / "facets.yaml").unlink()
    write_note(vault, "concepts/f.md", "id: n_f\ntype: concept\ntitle: F\nfacets: {anything: [goes]}")
    link_from_index(vault, "n_f")
    assert lint(vault) == []
