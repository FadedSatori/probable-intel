---
name: anamnesis-query
description: Answer questions from the user's own second brain, the Anamnesis vault, with citations to their notes and sources, and offer to file good answers back as notes. Use this whenever the user asks what they know, think, or have read about something ("what do I have on X", "what did I conclude about Y", "remind me why I believed Z", "how does A relate to B in my notes"), asks for a comparison or synthesis across their notes, or asks anything that should be grounded in their vault rather than general knowledge. Prefer it over a web search when the question is about the user's own knowledge.
---

# Query

Answer from the vault, not from general knowledge. The value of a second brain is that answers carry the user's own sources, reasoning and history. When the vault is silent, say so plainly, because that gap is itself useful information.

## Steps

1. **Orient.** Read `index.md`, which is the map. Then search with two to four phrasings, because a single query misses synonyms:
   ```bash
   pi brain search "personalized pagerank associative recall" -n 8
   pi brain search "hipporag multi-hop" --kind note
   ```
   - Use `--kind raw` to reach into original sources when the notes are thin.
   - If `qmd` is installed and configured on this vault, also run it for semantic matches.

2. **Read** the top notes in full. Follow one hop of `[[links]]` from the most relevant ones; related ideas often live next door.

3. **Answer** with citations:
   - Cite note ids inline, like `[[n_ppr-associative-recall]]`, and `raw:<sha>` when quoting a source.
   - Separate what the vault *says* (cited) from your own inference, labeled "inference".
   - Use claim probabilities and bands as recorded. If notes disagree, show both sides and their sources rather than averaging.
   - For "what did I believe before…" questions, use note history (`git log -p wiki/...`) and `log.md`.
   - If nothing relevant turns up, say "the vault has nothing on this". Then offer either to answer from general knowledge, clearly labeled as such, or to capture sources on it.

4. **Compound.** If the answer is a real synthesis (a comparison, a reconciled contradiction, a new connection), offer to file it as a note:
   - Create it in `wiki/concepts/` or `wiki/trails/` with `sources:` pointing at the notes and raws it drew on.
   - Add it to `index.md`.
   - Run `pi brain log query "<question>"`.

   Don't file trivial lookups.

## Guardrails
- **Read-only unless the user accepts filing.** Don't edit notes while answering.
- **Don't let the vault override the user.** If the user says something that contradicts a note, point out the discrepancy and ask which is current. Don't silently trust either side.
