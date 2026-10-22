# Anamnesis — Phase 0 quickstart

Phase 0 is a Markdown vault, a deterministic CLI (`pi brain`) and four Claude Code skills. There is no server or database to run: the only state outside your files is a search index the CLI rebuilds on demand.

## 1. Create the vault

```bash
pip install -e .                 # from this repo; puts `pi` on PATH
pi brain init ~/anamnesis
cd ~/anamnesis && git init && git add -A && git commit -m "vault: init"
```

What you get:

```
~/anamnesis/
├── constitution.md        # the rules: goals, trust tiers, note rubric, limits (you edit this; agents don't)
├── CLAUDE.md              # imports the constitution so Claude Code always has it in context
├── facets.yaml            # controlled vocabulary for tags
├── index.md  log.md       # catalog of notes; append-only operations log
├── inbox/                 # drop files here, then `pi brain capture --sweep`
├── raw/YYYY/MM/           # immutable sources + .meta.yaml sidecars (sha256, tier, status)
├── episodic/YYYY-MM.jsonl # event propositions, append-only
├── wiki/{concepts,entities,sources,projects,loops,predictions,ach,trails,maps,journal}/
├── beliefs/ retention/    # append-only ledgers (used from Phase 4/5)
└── .claude/skills/        # anamnesis-capture | -ingest | -query | -lint
```

**First thing to do:** fill in §1 *Purpose and active goals* in `constitution.md`. Ingest uses the goals to decide what matters.

## 2. Use it from Claude Code

Open the vault folder in Claude Code and talk normally:

| You say | Skill | What happens |
|---|---|---|
| "save this: https://…" / "capture the PDF in inbox" | capture | The source is fetched or read, then stored in `raw/` with a trust tier; nothing is summarized |
| "ingest the HippoRAG paper" / "process my inbox" | ingest | Event propositions go to `episodic/`; source and concept notes are created or revised and linked; the index and log are updated |
| "what do I know about associative recall?" | query | Searches, reads notes, answers with `[[note]]` and `raw:` citations; offers to file a good answer as a new note |
| "lint the vault" / "what's out of date?" | lint | Structural report plus contradictions and stale or overconfident claims; changes only what you approve |

## 3. The CLI directly

```bash
pi brain capture paper.pdf --tier T1 --title "Paper" --uri https://… --published 2025-02-20
pbpaste | pi brain capture - --tier T2 --title "Article" --channel clipper
pi brain capture --sweep                       # everything in inbox/
pi brain status                                # inbox + recent log
pi brain search "personalized pagerank" -n 5   # BM25 over notes and text sources
pi brain lint                                  # exit 1 on errors; --json for tools
pi brain band 0.7                              # → likely   (ICD-203)
pi brain surprise 0.5 0.9                      # → 0.5310 bits
pi brain mark raw:3fa9c1d2e4b5 distilled
pi brain log note "Wrote PPR note by hand"
```

All commands find the vault by walking up from the current directory, or take `--vault PATH`.

## 4. Habits that make it work

- **Daily:** capture freely, but ingest only what bears on a goal. The lint skill flags inbox items older than 14 days; expire them without guilt.
- **Weekly:** ask Claude to "lint the vault", and answer the contradictions it raises.
- **Commit often.** Git history is your "what did I believe in March" record until the Phase 4 belief ledger lands.

## What's next

- **Phase 1:** vector search with sqlite-vec, contextual chunk prefixes and a reranker.
- **Phase 2:** background consolidation.
- **Phase 3:** a graph with personalized-PageRank (PPR) retrieval.
- **Phase 4:** the belief ledger.
- **Phase 5:** spaced-repetition review.

See DESIGN.md §13.
