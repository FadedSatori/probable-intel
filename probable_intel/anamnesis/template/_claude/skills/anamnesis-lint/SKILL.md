---
name: anamnesis-lint
description: Health-check the Anamnesis second brain and propose fixes. It finds broken links, orphan notes, stale inbox items and frontmatter errors, and also contradictions between notes, claims made stale by newer sources, concepts mentioned without a page, overconfident single-source claims and open loops with no next action. Use this whenever the user asks to "lint", "clean up", "check", "tidy" or "audit" their vault or notes, asks "what's out of date" or "what contradicts what", after a large ingest, or as a weekly review step.
---

# Lint

A knowledge base rots quietly: links break, claims go stale, and two notes drift into disagreement without anyone noticing. Lint is the maintenance pass that keeps the vault trustworthy. It **reports and proposes**; it changes nothing without the user's OK. Some findings are judgment calls, such as which of two contradicting notes is right, and those belong to the user.

## Steps

### 1. Structural pass (deterministic)
```bash
pi brain lint --json
```
The output has two severity levels:
- **Errors:** missing files, bad frontmatter, duplicate ids, unresolvable `raw:` refs, claims with `p` out of range, bands that don't match `p`, claims with no sources.
- **Warnings:** broken or ambiguous links, orphans, notes missing from `index.md`, inbox items older than 14 days.

### 2. Semantic pass (your judgment)
Read `index.md` and sample the notes, prioritizing recently updated ones and those with claims. Look for:
- **Contradictions:** two notes or claims that can't both be true. Quote both and give their sources and tiers.
- **Stale claims:** a newer source in `log.md` or `episodic/` supersedes an older claim.
- **Missing pages:** a concept mentioned in several notes that has no note of its own.
- **Overconfidence:** a claim with `p > 0.8` resting on a single source or a single upstream (several articles quoting one press release count as one source). Propose a lower `p`, or a competing-hypotheses note if the stakes are high.
- **Open loops:** `type: loop` notes with no next action and no explicit "dropped" decision.
- **Collector's fallacy:** many captures and few distilled notes. Report the inbox-to-distilled ratio from `pi brain status`.
- **Rubric drift:** notes that aren't atomic, have source-shaped titles, or have no links.

### 3. Report
Group findings into three sections:
- **Fix now:** errors.
- **Your call:** contradictions, stale or overconfident claims.
- **Housekeeping:** orphans, index gaps, stale inbox.

For each finding, give the file, the problem, and a concrete proposed fix: the exact link to add, the `p` to change and why, or the note to split. Lead with what most threatens trust in the vault.

### 4. Apply what the user approves
Make only the approved edits, re-run `pi brain lint` to confirm it's clean, then log the run:
```bash
pi brain log lint "weekly lint" -d "errors fixed: N; contradictions raised: M; ratio: x"
```

## Guardrails
- **Never edit** `constitution.md`, `raw/`, or `locked: true` notes.
- **Never delete notes.** Propose archiving them to `wiki/journal/` with a reason instead.
- **Read before trusting a lint finding.** A "broken link" may be a deliberate placeholder for a note that should exist. Propose creating that note rather than removing the link.
