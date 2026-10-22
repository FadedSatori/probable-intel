# Anamnesis vault

A local-first second brain: plain Markdown + YAML + JSONL in Git. Built with `pi brain init`.

```bash
pi brain capture paper.pdf --tier T1 --title "Paper" --uri https://...   # store a source
pi brain status                                                          # what's in the inbox
pi brain search "query"                                                  # BM25 over notes + sources
pi brain lint                                                            # structural health check
```

Open this folder in Claude Code. The skills in `.claude/skills/` run capture, ingest, query and lint. Rules: `constitution.md`. Design: `docs/second-brain/DESIGN.md` in the probable-intel repo.

Git: commit everything except `.anamnesis/` (the search index is rebuilt from the files).
