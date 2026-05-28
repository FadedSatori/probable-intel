# /sprint — Execute the next ROADMAP item autonomously

Pick up and fully implement the next unchecked item from `ROADMAP.md`.

## Steps to execute

1. **Read ROADMAP.md** and find the first line matching `- [ ]` under "In Progress" or "Sprint Queue"

2. **Parse the item**: extract the feature name, the "Files:" list, the "Decision:" rationale, and the "Verify:" command

3. **Implement** — follow the files list and pre-made decisions exactly. Do not re-debate decisions that are recorded in the item or in `docs/decisions/`.

4. **Run the verification command** from the item (or the default below if none specified):
   ```bash
   python -m pytest tests/ -q --tb=short
   pi validate nexus/apparatuses/mvp-demo.nx
   pi validate nexus/apparatuses/cyber-collection.nx
   ```

5. **Mark done** — change `- [ ]` to `- [x]` for the completed item in ROADMAP.md and move it to the Completed section

6. **Commit** with message: `feat: <item name from ROADMAP>`

## Decision hierarchy (consult in order, stop at first match)
1. Decision recorded in the ROADMAP item itself
2. Relevant ADR in `docs/decisions/`
3. Pattern in an existing similar node (same tier)
4. CLAUDE.md key invariants
5. Ask the user
