# Anamnesis — a second brain built as a personal intelligence apparatus

> *Anamnesis* (Plato): knowledge as recollection. The system's job is not to store what you saw, but to make what you know **recoverable, current, calibrated, and generative**.

Status: design proposal · Date: 2026-10-22 · Target runtime: `probable-intel` (this repo)

---

## 0. Thesis in five lines

1. **A second brain is an intelligence cycle pointed inward.** Collection → processing → analysis → dissemination → feedback is the same loop as Capture → Organize → Distill → Express, and the intelligence community has 70 years of hard-won tradecraft for the parts PKM people still hand-wave (source reliability, estimative language, competing hypotheses, deception).
2. **Two memory systems, not one.** Neuroscience (Complementary Learning Systems) and the best agent-memory systems converge on the same split: a fast, verbatim, append-only *episodic* store plus a slow, consolidated, interlinked *semantic* store, connected by an offline replay/consolidation process.
3. **The LLM is the librarian, not the oracle.** Karpathy's LLM-Wiki insight: the bottleneck in every PKM system is bookkeeping. Let the model do the maintenance (linking, summarizing, contradiction-flagging); keep the human on curation, judgment, and expression.
4. **Every claim carries provenance, time, and a probability.** Bi-temporal facts (Graphiti/Datomic) + provenance-capped trust (memory-poisoning research) + ICD 203 estimative language + Brier-scored calibration = a *belief ledger*, the piece no existing PKM tool has.
5. **Plain Markdown on disk is the source of truth; everything else is a rebuildable index.** Local-first (Ink & Switch), Git-versioned, tool-agnostic. Obsidian, Claude Code, MCP, and `pi` are all just clients.

---

## 1. What the research actually says (and what it doesn't)

### 1.1 Findings that drive the design

| # | Finding | Source | Design consequence |
|---|---|---|---|
| F1 | RAG re-derives knowledge on every query; nothing accumulates. An LLM-maintained wiki (raw → wiki → schema; ops: ingest / query / lint) compounds instead. Works to ~100 sources / hundreds of pages on an `index.md` alone. | Karpathy, *llm-wiki* gist (2026-04-04) | Semantic layer is an LLM-maintained Markdown wiki. Answers get **filed back** as pages. `index.md` + `log.md` conventions adopted verbatim. |
| F2 | Hippocampus = fast, sparse, pattern-separated episodic learning; neocortex = slow, overlapping, structure-extracting. Replay integrates the two and can be goal-weighted. | Kumaran, Hassabis & McClelland, *CLS Theory Updated* (TiCS 2016) | Separate **episodic log** (append-only) from **semantic wiki**; a **consolidation job** ("sleep") replays episodes into the wiki, weighted by current goals. |
| F3 | Background "sleep-time" agents that own memory edits make the primary agent faster and more reliable; incremental memories get messy without consolidation. | Letta sleep-time agents (arXiv 2504.13171) | Memory writes are **asynchronous and owned by a dedicated consolidator**, not done inline during conversation. |
| F4 | Observer/reflector compression: an observer appends dated, prioritized observations to a stable log once history passes a token threshold; a reflector occasionally condenses. Stable prefix ⇒ prompt-cache friendly. Reported 94.87% LongMemEval (vendor-run, within variance of others). Weak for open-ended discovery. | Mastra, *Observational Memory* | Episodic log entries use the observation format (date, priority, terse proposition). Reflection is rare and batched. Do **not** rely on it for discovery — that is the graph's job. |
| F5 | Event-centric, **non-compressive** memory (short neo-Davidsonian event propositions: participants + time + minimal context) matches or beats complex systems with much shorter QA contexts. | Zhou & Han, arXiv 2511.17208 (EMem) | Episodic unit = **event proposition**, not a summary and not a bare triple. Keep it lossless; compress only in the semantic layer. |
| F6 | Zettelkasten-style agentic memory: each memory gets keywords, tags, context; new memories link to and **update** older ones. | Xu et al., *A-MEM* (NeurIPS 2025) | On ingest, the consolidator proposes links *and* revisions to existing notes (memory evolution), not just new notes. |
| F7 | Graph + Personalized PageRank over entity/passage graph gives associative ("multi-hop") recall without the factual-accuracy collapse of earlier GraphRAG variants. | Gutiérrez et al., *HippoRAG 2* (ICML 2025) | Retrieval stage 3 is **PPR from query-linked seed nodes**, over a graph that keeps passages as nodes. |
| F8 | Facts need two clocks: **valid time** (true in the world) and **transaction time** (when we learned it). Contradictions invalidate edges instead of deleting them. | Rasmussen et al., *Zep/Graphiti* (arXiv 2501.13956); Datomic | All claims/edges are **bi-temporal**; nothing is ever deleted, only superseded. "What did I believe on date X?" is a query. |
| F9 | Contextual chunk prefixes + BM25 + embeddings + rerank cut top-20 retrieval failure ≈67% (5.7% → 1.9%) in Anthropic's best config. | Anthropic, *Contextual Retrieval* (2024) | Every chunk gets a 1–2 sentence LLM-generated context prefix before both FTS and embedding indexing. Cross-encoder rerank is mandatory. |
| F10 | RRF is the robust way to fuse FTS5 and vector rankings in SQLite (raw scores aren't comparable). | Willison / Garcia, sqlite-vec hybrid search (2024) | Hybrid search in a **single SQLite file**: FTS5 + sqlite-vec + RRF. No vector DB server. |
| F11 | Trust must be capped by **source provenance**, not by how confident the text sounds; content-inferred trust is adversarially gameable. | arXiv 2606.22030; MemLineage (2605.14421); OWASP | `trust = min(provenance_tier, content_confidence)`. Untrusted captures are quarantined and can never raise a belief above their tier. |
| F12 | FSRS's DSR model (Difficulty, Stability, Retrievability) is the state of the art open scheduler; retrievability is computed, stability/difficulty are stored. | open-spaced-repetition / py-fsrs | Use FSRS **beyond flashcards**: for note resurfacing and for belief re-verification ("staleness"). |
| F13 | Embedding review prompts inside narrative (mnemonic medium) lifts first-review recall on hard items from ~42% to ~68–71%; median prompt review 6s. | Matuschak & Nielsen, Quantum Country notes | Prompts live **inside** evergreen notes (inline `Q:/A:` blocks), not in a separate deck. |
| F14 | Storage strength never decays; retrieval strength does. Effortful retrieval (testing, spacing, interleaving, generation) builds durable memory. | Bjork & Bjork, New Theory of Disuse; *Desirable Difficulties* | The UI defaults to **"recall first, then reveal"** for resurfaced notes. Search is not review. |
| F15 | Sensemaking = foraging loop (shoebox → evidence file) + sensemaking loop (schema → hypothesis → presentation), running both bottom-up and top-down. | Pirolli & Card (2005) | Pipeline stages are named after this model, and **top-down** queries (a hypothesis) can trigger new foraging. |
| F16 | Information foraging: follow scent; leave a patch when marginal gain drops below the environment average (Marginal Value Theorem). | Pirolli & Card, *Psych. Review* (1999) | Inbox items get a **scent score**; reading sessions get a "patch exhausted" signal. |
| F17 | Bayesian surprise = KL(posterior ‖ prior); surprising stimuli attract attention (72% of gaze shifts → above-average surprise). | Itti & Baldi (NeurIPS 2005) | Ingest priority and the weekly digest are ranked by **how much a source moved your beliefs**, not by recency. |
| F18 | ACH: matrix of hypotheses × evidence; judge evidence by **diagnosticity**; seek disconfirmation. | Heuer, CIA | `ACH` is a first-class note type with a generated matrix. |
| F19 | ICD 203 fixes estimative words to probability bands and separates **likelihood** from **confidence**. ODNI itself reports confidence levels are often misused. | ICD 203; ODNI research short (2024) | Claims store a numeric probability + band label; confidence is derived from evidence quality, never typed by hand. |
| F20 | Calibration improves only with scoring and feedback ("perpetual beta" ≈3× stronger than IQ as a predictor). | Tetlock, *Superforecasting*; Good Judgment | Predictions are resolved and **Brier-scored**; the system shows you your calibration curve. |
| F21 | Unfinished tasks stay cognitively active (Zeigarnik); a trusted capture system + weekly review closes loops. | Allen, *GTD* | **Open loops** are first-class objects with an owner, a next action or an explicit "dropped". |
| F22 | Collecting ≠ understanding (Collector's Fallacy). | Tietze; Ahrens, *How to Take Smart Notes* | Inbox items **decay**; capture-to-distill ratio is a tracked health metric. |
| F23 | The Zettelkasten as "communication partner" must be able to **surprise** its user; built between order and chaos. | Luhmann (1981) | A **serendipity engine** surfaces structurally distant-but-connected notes on purpose. |
| F24 | Evergreen notes: atomic, concept-oriented, densely linked, written for yourself; goal is better thinking, not better storage. | Matuschak, *Evergreen notes* | Note-writing rubric enforced by the linter. |
| F25 | Faceted classification (Ranganathan PMEST) beats single hierarchies; folksonomies drift without synonym control. | Ranganathan; ISKO | Tags are **faceted and aliased** (SKOS-style `prefLabel`/`altLabel`), not a free-for-all. |
| F26 | Transclusion and bidirectional links (Bush → Nelson → Roam/Logseq). Web annotations anchor robustly via `TextQuoteSelector` (exact + prefix + suffix). | Nelson; W3C Web Annotation; Hypothesis fuzzy anchoring | Quotes from raw sources are stored as W3C selectors, so a highlight survives source edits and links back to the original. |
| F27 | Local-first ideals: fast, multi-device, offline, collaborative, long-lived, private, user-owned. | Ink & Switch (2019) | Markdown + Git + SQLite file. Any sync (Git, Syncthing) works. No server required. |

### 1.2 What the research does *not* support — read before trusting a leaderboard

- **Vendor memory benchmarks are not comparable.** Mem0, Zep, Letta, ByteRover and Mastra all self-report 90%+ on LoCoMo/LongMemEval; independent reruns are far lower (e.g. Mem0 OSS ≈32% on LongMemEval in one independent test vs. 93% self-reported), and a cited audit found the LoCoMo judge accepts up to 63% of deliberately wrong answers and ≈6.4% of ground truth is wrong. → **We build our own eval set from your own corpus (§9).**
- **GraphRAG is not free accuracy.** Earlier graph RAG variants lost basic factual recall vs. plain RAG; HippoRAG 2 was explicitly built to fix that. → Graph expansion is a *stage* after hybrid search, never a replacement for it.
- **Reconsolidation is a metaphor, not a mechanism.** The neuroscience (retrieval re-opens a memory for update) is real but contested on replication; we borrow the *design pattern* (edit-on-access) without claiming biological fidelity.
- **Several secondary numbers here are vendor- or blog-sourced** (flagged in §12). Treat them as priors, not facts.

---

## 2. Design principles (each traceable to §1)

1. **Raw is sacred.** Sources are immutable and content-addressed. The LLM reads them, never edits them. (F1, F8)
2. **Lossless below, lossy above.** Episodic events are verbatim-faithful propositions; compression happens only when writing semantic notes. (F2, F5)
3. **Writes are asynchronous; reads are synchronous.** The thing you talk to never edits memory mid-thought; a consolidator does it in "sleep". (F3, F4)
4. **Nothing is deleted, only superseded.** Bi-temporal everything. (F8)
5. **Provenance caps trust.** No evidence chain is stronger than its weakest link, and no claim is more trusted than its best independent chain. (F11)
6. **Probabilities, not adjectives.** Every factual claim has a numeric `p` and an ICD-203 band. (F19, F20)
7. **Retrieval is a pipeline, not a call.** Lexical + semantic + graph + rerank + diversity. (F7, F9, F10)
8. **Review ≠ search.** Resurfacing asks before it tells. (F13, F14)
9. **Surprise is signal.** Prioritize what changed your mind. (F17, F23)
10. **Close every loop.** Every capture is distilled, archived, or expires; every open loop has a next action or is consciously dropped. (F21, F22)
11. **Markdown is the API.** If the software dies, the notes still read fine in any editor in 2046. (F27)

---

## 3. Architecture

```
                         ┌──────────────────────────── SCHEMA (constitution.md) ───────────────────────────┐
                         │  note rubric · facet vocab · trust tiers · ICD-203 bands · lint rules · goals    │
                         └───────────────────────────────────────────────────────────────────────────────────┘
 FORAGE                   PROCESS                         CONSOLIDATE ("sleep")              SENSEMAKE / EXPRESS
 ───────                  ───────                         ─────────────────────              ───────────────────
 Feed/Web/Api/Social ─┐                                    ┌──────────────────────┐
 Clipper / email ─────┤   L0 RAW (immutable, sha256) ───►  │  Consolidator        │ ──►  L2 WIKI (Markdown)
 Voice / chat ────────┼─► quarantine (trust tier) ──────►  │   replay episodes    │       evergreen notes,
 Highlights (W3C) ────┘        │                           │   link + revise      │       entity/concept pages,
                               ▼                           │   (A-MEM evolution)  │       ACH, open loops,
                     L1 EPISODIC LOG (append-only)  ─────► │   surprise scoring   │       index.md, log.md
                     event propositions, bi-temporal       └─────────┬────────────┘            │
                               │                                     │                         │
                               └──────────┬──────────────────────────┘                         │
                                          ▼                                                    ▼
                     L3 INDEX (one SQLite file, rebuildable from L0–L2)            L5 RETENTION (FSRS state)
                     FTS5 · sqlite-vec · claim/edge tables · PPR graph             notes · prompts · beliefs
                                          │
                                          ▼
                     L4 BELIEF LEDGER  claims(p, band, confidence, provenance, valid/tx time) · Brier log
                                          │
                     ┌────────────────────┴────────────────────────────┐
                     ▼                                                 ▼
               Query / chat (MCP, CLI, Obsidian)              Linter · Weekly review · Digest
               answers filed back to L2 ────────────────────► (contradictions, orphans, stale beliefs,
                                                               open loops, surprise ranking)
```

### 3.1 Layers

| Layer | What | Storage | Owner | Mutability |
|---|---|---|---|---|
| **L0 Raw** | Original sources: articles, PDFs, transcripts, chat exports, voice notes | `raw/YYYY/MM/<sha256-prefix>-<slug>.<ext>` + `.meta.yaml` sidecar | Human (curation) | Immutable |
| **L1 Episodic** | Event propositions + observations extracted from raw and from your own sessions | `episodic/YYYY-MM.jsonl` (append-only) | Ingest pipeline | Append-only |
| **L2 Wiki** | Evergreen notes, entity pages, concept pages, ACH, open loops, project pages, `index.md`, `log.md` | `wiki/**/*.md` with YAML frontmatter | LLM consolidator, human edits always win | Versioned (Git) |
| **L3 Index** | FTS5, vectors, graph, chunk contexts | `.anamnesis/index.db` (SQLite) | System | Disposable / rebuildable |
| **L4 Beliefs** | Claim versions with probability, evidence chains, bi-temporal validity; predictions + resolutions | `beliefs/ledger.jsonl` (append-only, one line per claim version) → projected into `claim`/`prediction` tables | Consolidator proposes, human confirms above threshold | Append-only; superseded, never deleted |
| **L5 Retention** | Review events for notes, inline prompts, beliefs | `retention/reviews.jsonl` (append-only) → FSRS state recomputed by replay into `retention` table | Scheduler | Append-only |
| **Schema** | The constitution: rubric, vocab, trust tiers, goals, lint rules | `constitution.md` (+ `facets.yaml`) | **Human only** (LLM may propose diffs) | Versioned |

**Rule:** every file under version control is the truth; `index.db` is a pure projection. `anamnesis rebuild` must reproduce it bit-for-bit-equivalent from L0–L2 + `beliefs/ledger.jsonl` + `retention/reviews.jsonl`. If the DB is lost, nothing of value is lost. Note frontmatter shows only the **current** version of each claim (`claim_id` + `p`); history lives in the ledger, so there is exactly one writer per fact.

### 3.2 Why this shape (cross-domain synthesis)

| Domain | Concept | Becomes |
|---|---|---|
| Neuroscience | Hippocampus / neocortex / sleep replay (CLS) | L1 episodic / L2 wiki / consolidator job |
| Neuroscience | Hippocampal indexing theory (HippoRAG) | L3 graph as an *index into* L2, not a copy of it |
| Neuroscience | Reconsolidation | **Edit-on-access**: every retrieval of a note queues a micro-lint of that note |
| Intelligence | Intelligence cycle; source reliability grading | Pipeline stages; provenance trust tiers |
| Intelligence | ICD 203, ACH, Words of Estimative Probability | Belief ledger; ACH note type |
| Counter-intelligence | Deception, poisoning, compartmentation | Quarantine tier; provenance-capped trust; `trust_level` compartments (reuse `IntelPacket.trust_level`) |
| Forecasting | Brier score, perpetual beta | Prediction notes + calibration dashboard |
| Information theory | Bayesian surprise (KL) | Ingest priority + "what changed my mind" digest |
| Ecology | Optimal foraging / Marginal Value Theorem | Scent scores; patch-exhaustion signal while reading |
| Learning science | FSRS, desirable difficulties, mnemonic medium | Retention layer; recall-first resurfacing; inline prompts |
| Library science | Faceted classification, SKOS, controlled vocab | `facets.yaml` with preferred/alternate labels |
| Hypertext | Memex trails, Xanadu transclusion, W3C annotation | `trail` notes; block transclusion `![[note#^id]]`; TextQuoteSelector anchors |
| Databases | Datomic immutable facts, event sourcing, bi-temporality | L1 log + bi-temporal claims |
| Software | Local-first, Git, CRDT | Markdown + Git; Automerge only if real-time multi-device editing is ever needed |
| Productivity | GTD open loops, weekly review; PARA; CODE | Open-loop objects; weekly review job; PARA as *one* facet, not the folder tree |
| Philosophy | Luhmann's "communication partner" | Serendipity engine; the system argues back (devil's-advocate lint) |
| Taoism (*wu wei*) | Act through least forcing | Default to **no** manual filing: capture is one keystroke, organization emerges from consolidation; the human intervenes only where judgment is needed |

---

## 4. The loops

The system runs five loops at different clock speeds, the way the brain does.

| Loop | Clock | Trigger | Does | Pirolli–Card stage |
|---|---|---|---|---|
| **Capture** | Seconds | You clip/speak/forward/highlight | Store raw (sha256), assign trust tier, compute scent, extract event propositions → L1 | Shoebox |
| **Ingest** | Minutes | New raw or N new episodes | Contextual chunking, embed, FTS, entity linking, Bayesian-surprise score | Evidence file |
| **Consolidate** ("sleep") | Hours / nightly | Episode backlog > threshold, or nightly | Replay episodes weighted by active goals; create/revise notes (A-MEM evolution); update beliefs; supersede contradicted claims; append `log.md` | Schema |
| **Lint** | Nightly + on-access | Schedule; any note read | Contradictions, stale claims, orphans, missing pages, rubric violations, broken anchors, open loops without next actions | Schema ↔ evidence (top-down) |
| **Review** | Daily (5 min) / weekly (30 min) | FSRS due queue; Sunday | Recall-first resurfacing; resolve predictions (Brier); close open loops; read surprise digest | Hypothesis → presentation |

The **Express** step (writing, deciding, building) is where value is realized; every loop exists to feed it (Forte). Answers you generate in chat are filed back as notes (Karpathy) so exploration compounds.

---

## 5. Data model

### 5.1 Raw sidecar — `raw/2026/10/3fa9c1-some-article.meta.yaml`

```yaml
sha256: 3fa9c1...                # identity; reuse IntelPacket.source_hash semantics
uri: https://example.org/post
captured_at: 2026-10-22T14:03:11Z     # transaction time
published_at: 2026-10-20               # valid-time hint
channel: clipper                       # clipper|feed|api|social|voice|chat|email|manual
trust_tier: T2                         # see §6.1
facets: {domain: [epistemology], form: [essay]}
scent: 0.71                            # §7.3
status: inbox                          # inbox → distilled | archived | expired
```

### 5.2 Episodic event (L1) — one JSON line

Event-centric, non-compressive (F5), in observation format (F4):

```json
{"id":"ev_01JB...","tx_time":"2026-10-22T14:05:02Z","valid_time":"2026-10-20",
 "priority":"high","proposition":"Zhou & Han report event-centric memory matches complex systems on LongMemEval_S with shorter contexts.",
 "participants":["Zhou","Han","EMem"],"source":"raw:3fa9c1","anchor":{"type":"TextQuoteSelector","exact":"...","prefix":"...","suffix":"..."},
 "trust_tier":"T2","session":null}
```

### 5.3 Wiki note (L2) — frontmatter contract

```markdown
---
id: n_ppr-associative-recall          # stable; filename may change
type: concept        # concept|entity|source|ach|loop|prediction|project|trail|moc|journal
title: Personalized PageRank gives associative recall
facets: {domain: [information-retrieval, neuroscience], para: resource}
aliases: [PPR retrieval, hippocampal indexing]
created: 2026-10-22
updated: 2026-10-22
sources: [raw:3fa9c1, raw:9be210]
claims:                          # projection of the CURRENT version only; history is in beliefs/ledger.jsonl
  - id: c_hipporag2_assoc_gain
    text: HippoRAG 2 improves associative-memory tasks ~7 points over a SOTA embedding baseline.
    p: 0.80                      # probability the claim is true as stated
    band: likely                 # ICD-203 band, derived from p
    confidence: moderate         # derived from evidence quality, never hand-typed
    cap: T2                      # best independent evidence chain (§6.1)
stakes: normal                   # high → band changes need human confirmation
retention: {fsrs: true}          # include this note in resurfacing
---

Atomic, concept-oriented body written for future-you. Link densely: [[hippocampal-indexing-theory]].

Q: Why does PPR help multi-hop recall that dense retrieval misses?
A: It spreads activation from query-linked seed nodes through the graph, so passages two hops away score high even with no lexical/semantic overlap.
```

Inline `Q:/A:` blocks are the mnemonic medium (F13): they're extracted into FSRS cards but live with the idea.

### 5.4 Index schema (L3/L4/L5) — SQLite

```sql
-- documents & chunks (rebuildable)
CREATE TABLE chunk (
  id INTEGER PRIMARY KEY, note_id TEXT, raw_sha TEXT, ord INT,
  context TEXT,            -- LLM-generated 1–2 sentence situating prefix (Contextual Retrieval)
  body TEXT
);
CREATE VIRTUAL TABLE chunk_fts USING fts5(context, body, content='chunk', content_rowid='id');
CREATE VIRTUAL TABLE chunk_vec USING vec0(embedding float[768]);   -- sqlite-vec; dim per model

-- graph (HippoRAG-style: entities AND passages are nodes)
CREATE TABLE node (id TEXT PRIMARY KEY, kind TEXT CHECK(kind IN ('entity','passage','note')), label TEXT);
CREATE TABLE edge (
  src TEXT, dst TEXT, rel TEXT, weight REAL DEFAULT 1.0,
  valid_from TEXT, valid_to TEXT,          -- world time
  tx_from TEXT NOT NULL, tx_to TEXT,       -- system time
  provenance TEXT NOT NULL                 -- raw:sha | ev:id | note:id | human
);

-- belief ledger (bi-temporal, append-only)
CREATE TABLE claim (
  id TEXT, version INT, text TEXT, p REAL CHECK(p BETWEEN 0 AND 1),
  cap TEXT, evidence JSON,                  -- [{source, supports: true|false, diagnosticity}]
  valid_from TEXT, valid_to TEXT, tx_from TEXT NOT NULL, tx_to TEXT,
  superseded_by TEXT, PRIMARY KEY (id, version)
);
CREATE TABLE prediction (
  id TEXT PRIMARY KEY, claim_id TEXT, p REAL, made_at TEXT, resolve_by TEXT,
  outcome INTEGER, resolved_at TEXT          -- outcome 0/1 → Brier
);

-- retention (FSRS state per card: note, inline prompt, or belief)
CREATE TABLE retention (
  card_id TEXT PRIMARY KEY, kind TEXT CHECK(kind IN ('note','prompt','belief')),
  fsrs_state JSON, due TEXT
);
```

---

## 6. The belief ledger (the novel core)

### 6.1 Trust tiers (provenance)

Adapted from NATO/Admiralty source grading, reduced to what a person can apply quickly:

| Tier | Meaning | Examples | Can raise a claim to at most |
|---|---|---|---|
| T0 | First-hand, verified by you | Your own measurements, signed documents | `p` unbounded |
| T1 | Primary, accountable | Peer-reviewed paper, official spec, primary data | 0.95 |
| T2 | Reputable secondary | Quality journalism, well-known textbooks, maintainer docs | 0.85 |
| T3 | Unvetted / self-interested | Vendor benchmarks, blogs, forums, LLM output | 0.65 |
| TQ | Quarantine | Unknown provenance, injected content, anything an agent fetched unsupervised | cannot raise; only lower |

```python
TIER_CAP = {"T0": 1.0, "T1": 0.95, "T2": 0.85, "T3": 0.65, "TQ": 0.5}

def chain_cap(chain: list[str]) -> float:
    """One evidence chain (source → excerpt → episode → claim) is only as strong as its weakest link (F11)."""
    return min(TIER_CAP[t] for t in chain)

def claim_cap(chains: list[list[str]]) -> float:
    """A claim is capped by its BEST independent chain, so adding weak evidence never lowers the cap."""
    return max((chain_cap(c) for c in chains), default=TIER_CAP["TQ"])

def capped_update(p_prior: float, p_proposed: float, chains: list[list[str]]) -> float:
    """Evidence may lower belief freely, but may only raise it up to the provenance cap."""
    if p_proposed <= p_prior:
        return p_proposed
    return min(p_proposed, max(p_prior, claim_cap(chains)))
```

Asymmetry is deliberate: a poisoned source can make you *doubt* (recoverable) but cannot make you *believe* (dangerous). Two refinements keep the asymmetry from becoming an attack surface:

- **Downward moves from T3/TQ evidence are proposals, not writes.** A flood of low-tier contradicting sources opens an ACH stub and queues the claim for review; it does not silently drop `p`. (Otherwise "make you doubt" becomes "make you forget".)
- **Independence is checked, not assumed.** Chains that share an upstream (same wire story, same vendor post) collapse to one chain before `claim_cap` and before confidence is computed.

**Who sets `p`.** An LLM's stated probabilities are not calibrated. The consolidator *proposes* `p`; claims that cross a band boundary, or touch a note marked `stakes: high`, need human confirmation. The LLM's proposals are Brier-scored separately from yours (§6.5), so you learn how far to trust its numbers.

### 6.2 Estimative language (ICD 203)

```python
ICD203 = [  # (upper bound, label)
    (0.05, "almost no chance"), (0.20, "very unlikely"), (0.45, "unlikely"),
    (0.55, "roughly even chance"), (0.80, "likely"), (0.95, "very likely"), (1.00, "almost certain"),
]

def band(p: float) -> str:
    return next(label for ub, label in ICD203 if p <= ub)
```

Confidence (low / moderate / high) is computed from evidence count, tier mix, and independence of sources (do they share an upstream?), never entered by hand (F19).

### 6.3 Bayesian surprise — what changed my mind

For a binary claim moving from prior `p` to posterior `q`, surprise is the KL divergence between the two Bernoulli beliefs:

```python
from math import log

def surprise_bits(p: float, q: float, eps: float = 1e-6) -> float:
    """KL(Bern(q) || Bern(p)) in bits: how far this evidence moved the belief (F17)."""
    p, q = min(max(p, eps), 1 - eps), min(max(q, eps), 1 - eps)
    return (q * log(q / p) + (1 - q) * log((1 - q) / (1 - p))) / log(2)
```

A source's surprise = sum over the claims it touched. The weekly digest is sorted by this, so the first thing you read is the stuff that actually changed your model of the world, not the stuff that was merely new.

### 6.4 ACH notes

`type: ach` notes hold hypotheses and evidence; the system renders the matrix and computes per-hypothesis inconsistency (Heuer: reject the hypothesis with the **most inconsistent** evidence, rather than accepting the one with the most consistent). Evidence rows with equal scores across all hypotheses are flagged **non-diagnostic** and greyed out.

### 6.5 Calibration

`type: prediction` notes are claims with a `resolve_by` date. On resolution: Brier `(p - outcome)^2`. The review dashboard shows a reliability diagram (bucketed `p` vs. observed frequency) per facet, so you learn *where* you are over-confident.

---

## 7. Retrieval

### 7.1 Pipeline

```
query
 ├─ rewrite: expand aliases from facets.yaml; HyDE-style hypothetical note (optional)
 ├─ stage 1  lexical  : FTS5 BM25 over context+body                ─┐
 ├─ stage 2  semantic : sqlite-vec KNN over contextualized chunks   ─┴─► RRF (k=60)
 ├─ stage 3  graph    : seed = top RRF nodes + query-linked entities → Personalized PageRank
 ├─ stage 4  rerank   : cross-encoder (local) over top ~150 → top ~20
 ├─ stage 5  diversity: MMR (λ≈0.7) so one source can't fill the window
 ├─ stage 6  temporal : drop claims with tx_to/valid_to set unless the query is historical ("as of")
 └─ answer with citations to note ids + raw anchors; offer "file this answer as a note"
```

### 7.2 Code to steal / write

```python
def rrf(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion: robust to incomparable score scales (F10)."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


import networkx as nx

def ppr_expand(G: nx.Graph, seeds: dict[str, float], damping: float = 0.5, top: int = 50):
    """HippoRAG-style associative recall (F7). Low damping keeps mass near the seeds;
    tune on your own eval set (§9) rather than trusting a default."""
    pr = nx.pagerank(G, alpha=damping, personalization=seeds, weight="weight")
    return sorted(pr.items(), key=lambda kv: kv[1], reverse=True)[:top]
```

`networkx` is already a dependency of this repo (`KnowledgeGraphNode`); `nx.pagerank` additionally needs `scipy` at runtime, which is not yet in `pyproject.toml`.

### 7.3 Scent (foraging) score for inbox items

`scent = w1·goal_similarity + w2·novelty + w3·trust + w4·facet_match − w5·age_decay`, where `goal_similarity` is cosine to the embeddings of your active projects/goals in `constitution.md` and `novelty` is `1 − max cosine` to existing notes. Items under a floor auto-expire to `archived` after N days unless touched (Collector's Fallacy guard, F22).

---

## 8. Serendipity, resurfacing, and review

### 8.1 Retention (FSRS everywhere)

Use the maintained **py-fsrs** library as a dependency (don't reimplement):

- **prompt** cards: inline `Q:/A:` blocks → classic spaced repetition, recall-first.
- **note** cards: evergreen notes flagged `retention.fsrs: true` resurface on schedule. Rating = "could I state the gist before opening it?" (generation effect, F14).
- **belief** cards: high-stakes claims resurface for **re-verification**. Stability grows when re-checked evidence holds, shrinks when a contradiction appears. This turns staleness into a scheduled, prioritized queue instead of a vague worry.

### 8.2 Serendipity engine (Luhmann, F23)

Daily "surprise me" card: pick a note you touched today, run PPR from it, then choose a result with **high PPR score but low embedding similarity**, i.e. structurally connected but semantically distant. That is exactly the "unexpected but not random" region Luhmann designed the Folgezettel for. Present it as a question: *"How does X relate to Y?"* Your answer becomes a new link note.

### 8.3 Devil's-advocate lint

For any claim with `p > 0.8` and a single upstream source, the linter generates the strongest counter-hypothesis and opens an ACH note stub. The system argues back.

### 8.4 Weekly review (GTD × intel)

1. **Open loops**: every `type: loop` without a next action or a "dropped" decision is listed (F21).
2. **Predictions due**: resolve → Brier.
3. **Surprise digest**: top sources by Bayesian surprise (§6.3).
4. **Health**: capture→distill ratio, orphan rate, contradiction count, stale-belief queue, inbox age histogram.
5. **Constitution edits**: proposed rubric/facet changes from the linter; you accept or reject.

---

## 9. Evaluation — measure on *your* corpus

Vendor numbers are untrustworthy (§1.2), so the system ships its own harness:

| Metric | How | Target to start |
|---|---|---|
| Retrieval recall@10 | 100+ questions you write about your own notes (single-hop, multi-hop, temporal "what did I think in March", abstention "I never wrote about this") | ≥ 0.85 |
| Answer faithfulness | Every sentence in an answer must cite a note/raw anchor; auto-check citation entailment | ≥ 0.95 cited |
| Temporal correctness | Questions whose answer changed over time; must return current *and* historical correctly | ≥ 0.90 |
| Abstention | Questions with no answer in corpus; must say so | ≥ 0.90 |
| Calibration | Brier score over resolved predictions; reliability diagram | Improving quarter on quarter |
| Hygiene | Orphan rate, capture→distill ratio, inbox median age | < 5% orphans; ratio ≥ 0.3 |
| Poisoning | Inject T3/TQ documents asserting falsehoods; measure belief movement | No belief exceeds its tier cap |

The question categories mirror LongMemEval's (information extraction, multi-session reasoning, temporal reasoning, knowledge updates, abstention) so you can compare shape without trusting their numbers. Every pipeline change runs the harness in CI (`pytest -m eval`, opt-in).

---

## 10. Building it on probable-intel

This repo already has most of the plumbing: an async packet bus, harvesters, an entity extractor, an in-memory knowledge graph, SQLite storage with `source_hash` dedup, an LLM router with budget guard, an OODA task router, counter-intel nodes, and a NEXUS DSL. A second brain is an apparatus.

| Second-brain function | Existing node | New node (proposed) |
|---|---|---|
| Capture | `FeedNode`, `WebNode`, `ApiNode`, `SocialNode` | `InboxNode` (clipper/email/voice/manual drop-folder watcher) |
| Raw store | `StorageNode` (`INSERT OR IGNORE` on `source_hash`) | extend to write `raw/` + sidecar |
| Entity linking | `EntityExtractorNode` | — |
| Episodic extraction | — | `EpisodeNode` (event propositions, observation format) |
| Index | — | `IndexerNode` (contextual chunking, FTS5, sqlite-vec) |
| Graph | `KnowledgeGraphNode` (NetworkX) | add bi-temporal edges + PPR query |
| Consolidation | `NarrativeNode` (LLM synthesis) | `ConsolidatorNode` (sleep-time, A-MEM evolution, writes `wiki/`) |
| Lint | `AnomalyNode` pattern | `LinterNode` (contradictions, orphans, rubric, edit-on-access) |
| Beliefs | `ThreatAssessNode` rule engine | `BeliefNode` (capped updates, ICD-203 bands, surprise) |
| Review | `AlertNode` (delivery) | `ResurfaceNode` (FSRS queue, serendipity, weekly digest) |
| Goal-driven foraging | `TaskRouterNode` (OODA) | reuse: active goals in constitution → collection directives |
| Poisoning defense | `OpSecNode`, `trust_level` on `IntelPacket` | map tiers T0–TQ onto `TrustLevel` |
| Clients | `HubAPI`, `pi` CLI | MCP server exposing `search`, `read_note`, `file_answer`, `capture`, `as_of` |

Sketch apparatus:

```yaml
apparatus_name: "anamnesis"
version: 1.0
trust_level: restricted
owner: "satori"

nodes:
  - type: InboxNode
    id: "capture.inbox"
    targets: [{ path: "~/anamnesis/inbox" }]
    emit: { channel: "raw.capture", priority: normal }

  - type: FeedNode
    id: "capture.feeds"
    targets: [{ feed: "https://example.org/feed.xml" }]
    schedule: { interval: "1h", jitter: 120 }
    emit: { channel: "raw.capture", priority: low }

  - type: EpisodeNode
    id: "process.episodes"
    subscribe: { channels: ["raw.capture"] }
    emit: { channel: "mem.episodic", priority: normal }
    llm: { provider: "ollama", model: "llama3.2", base_url: "http://localhost:11434", budget_per_day_usd: 0.0 }

  - type: ConsolidatorNode
    id: "sleep.consolidate"
    subscribe: { channels: ["mem.episodic"] }
    schedule: { interval: "6h", jitter: 600 }
    emit: { channel: "mem.semantic", priority: low }
    llm: { provider: "anthropic", model: "<frontier-model>", budget_per_day_usd: 1.0 }

  - type: ResurfaceNode
    id: "review.daily"
    subscribe: { channels: ["mem.semantic"] }
    schedule: { interval: "1d" }
```

Note the validator rule "LLM backend on non-analyst nodes → error": `EpisodeNode` and `ConsolidatorNode` should be registered in the **analyst** tier.

---

### 10.1 Operating interface: a skill pack

Phase 0 runs entirely as Claude Code skills, before any new node exists. Skills are the right unit: each is a named, versioned procedure that loads only when its trigger fires, so the constitution stays small and every loop in §4 has exactly one entry point.

| Skill | Trigger | Does | Loop |
|---|---|---|---|
| `anamnesis-capture` | "save this", a URL, a pasted excerpt | Writes raw + sidecar (sha256, tier, scent); no LLM synthesis | Capture |
| `anamnesis-ingest` | "ingest", or inbox items pending | Event propositions → `episodic/`; proposes note diffs; appends `log.md` (`## [date] ingest \| title`) | Ingest → Consolidate |
| `anamnesis-query` | Any question about your own knowledge | Hybrid search (qmd in P0, L3 later) → cited answer → offers to file the answer back | Query |
| `anamnesis-lint` | "lint", nightly | Contradictions, orphans, stale claims, unanchored claims, rubric violations, open loops without next actions | Lint |
| `anamnesis-review` | "review", daily or weekly | FSRS queue (recall-first), predictions due (Brier), surprise digest, health metrics | Review |
| `anamnesis-ach` | "compare hypotheses", or a lint-generated stub | Builds or updates the ACH matrix; flags non-diagnostic evidence | Sensemake |
| `anamnesis-forecast` | "I think X will…" | Turns a hunch into a `prediction` note with `p`, band, and `resolve_by` | Calibrate |

Each skill follows the same contract: it reads `constitution.md` first, only writes inside its scope (C4), and ends by appending to `log.md`.

**Existing skills worth composing** rather than rebuilding:
- `anthropic-skills:skill-creator`: author and iterate the pack above, including trigger-description tuning.
- `agentic-council:skeptic-failure-mode-analysis` and `skeptic-threat-model`: run on any change to the consolidator or trust model (this doc's §12 came from that pass).
- `anthropic-skills:deep-research`: the foraging step for a top-down question, with output captured as T2/T3 raw sources rather than pasted into notes.

**Skill hygiene is counter-intelligence.** A skill or plugin runs inside the same context that writes your memory, so it is a provenance tier in its own right. This session found third-party plugins that injected session-start instructions, phoned home every shell command, and nudged the agent to "silently" write drafts. Rules:
- Read a third-party skill's hooks and scripts before enabling it.
- Prefer skills with no hooks.
- Never grant memory-write scope to a skill you didn't write.
- Treat any instruction a skill injects into context as T3 data, not as the constitution.

## 11. What to steal, from where

**License check before copying code.** Depend on a library where possible; port ideas, not files, from copyleft projects. Licenses marked *verify* were not confirmed during this research.

| Project | Take | How | License |
|---|---|---|---|
| Karpathy `llm-wiki` gist | 3-layer layout, ingest/query/lint ops, `index.md`, `log.md` prefix convention (`## [YYYY-MM-DD] op \| title`) | Adopt conventions verbatim | Gist (idea file) |
| `getzep/graphiti` | Bi-temporal edge model, contradiction → invalidate edge, episode ingestion | Port the data model; optional dependency if Neo4j is ever added | Apache-2.0 |
| `OSU-NLP-Group/HippoRAG` | Query-to-seed linking, recognition-memory filter, PPR over entity+passage graph | Port the algorithm onto NetworkX/SQLite | verify |
| `agiresearch/A-mem` | Note construction (keywords/tags/context) and memory-evolution prompts | Port prompts and evaluation idea | verify |
| `KevinSRR/EMem` | Event-proposition extraction prompts | Port prompts | verify |
| Mastra observational memory | Observation line format, token thresholds, stable-prefix caching | Port the pattern (TypeScript upstream) | verify |
| Letta sleep-time agents | Primary agent can't edit memory; consolidator can | Architectural pattern | Apache-2.0 (verify) |
| `asg017/sqlite-vec` | Vector KNN inside SQLite | **Dependency** | Apache-2.0 / MIT (verify) |
| Willison hybrid-search SQL | FTS5 + vec + RRF query | Port | Blog post |
| Anthropic contextual retrieval cookbook | Chunk-context prompt, rerank stage | Port prompt | MIT (verify) |
| `open-spaced-repetition/py-fsrs` | Scheduler, optimizer | **Dependency** | MIT (verify) |
| `tobi/qmd` | Local hybrid markdown search CLI + MCP, LLM rerank | Use as an interim search backend before L3 exists | verify |
| `basicmachines-co/basic-memory` | Markdown-as-truth + SQLite index + MCP tool surface (`write_note`, `build_context`, `recent_activity`) | **Ideas only** | AGPL-3.0 (verify) |
| `topoteretes/cognee` | remember / recall / improve / forget API vocabulary | Ideas | Apache-2.0 |
| Hypothesis `dom-anchor-text-quote` | Robust quote anchoring | Port selector logic to Python | BSD-2 (verify) |
| `AgriciDaniel/claude-obsidian`, `Astro-Han/karpathy-llm-wiki` | Claude Code skill layout for an LLM wiki | Read for prompt ideas | verify |

**Tests worth stealing (patterns, not files):** Graphiti's temporal-invalidation tests (assert old edge gets `invalid_at`, not deleted); py-fsrs's scheduler property tests (intervals monotone in rating); HippoRAG's multi-hop eval harness shape; Basic Memory's round-trip test (Markdown → index → Markdown is lossless). Add our own: **poisoning property test**: for any sequence of T3/TQ evidence, `max(p) ≤ TIER_CAP[T3]`.

---

## 12. What breaks: failure-mode analysis

Method: enumerate components, list failure modes per component (network, data, resource, dependency, state), map cascades, then define mitigation, detection, and rollback. The four **design defects** this pass found have already been fixed above. They are listed first because they would have shipped silently.

### 12.1 Design defects found and fixed

| Defect | Why it was wrong | Fix |
|---|---|---|
| `trust_cap = min` over **all** evidence | One quarantined source would cap a claim backed by a T1 paper at 0.5, which handed attackers a downgrade primitive | Cap per chain = min; claim cap = **max over independent chains**; low-tier downgrades become review proposals (§6.1) |
| Belief and review history lived only in `index.db`, which was declared disposable | `rebuild` would have silently destroyed the history that makes "what did I believe in March" and FSRS work | Append-only `beliefs/ledger.jsonl` and `retention/reviews.jsonl` are the truth; the DB is a projection (§3.1) |
| Claims stored in both frontmatter and the DB with no single writer | Divergence was guaranteed after the first manual edit | Frontmatter = current-version projection; ledger = history; one writer (§5.3) |
| Consolidator reads untrusted text **and** can write anywhere | A prompt injection in a clipped page could rewrite `constitution.md`, i.e. the system's own rules | Constitution is human-only; consolidator write scope = `wiki/` + ledger proposals; source text is passed as quoted data (§12.2, C4) |

### 12.2 Failure-mode table

| # | Component | Failure mode | Sev. | Cascade | Mitigation | Detection signal |
|---|---|---|---|---|---|---|
| C1 | Capture | LLM/provider down, budget exhausted | High | Episodes and consolidation stall | **Capture never calls an LLM.** Raw + sidecar + FTS are written synchronously; LLM stages are queued with backoff + jitter (reuse `BaseNode` backoff and `LLMRouter` budget guard) | Queue depth; `episodes_pending > N` for 24h |
| C2 | Capture | Duplicate / near-duplicate sources | Low | Inflated corroboration | sha256 dedup (`INSERT OR IGNORE`, existing); near-dup via MinHash on normalized text; dup chains collapse before `claim_cap` | Dup rate per channel |
| C3 | Raw store | Disk full; Git bloat from PDFs/audio | Med | Commits fail → wiki edits lost | Raw binaries outside Git (Git LFS, or plain dir + `raw/MANIFEST.sha256` in Git); prune derived caches first | Free-space alert at 10%; repo size |
| C4 | Consolidator | **Prompt injection** from source text | Critical | Corrupted notes, poisoned beliefs, rewritten rules | Write scope limited to `wiki/` + ledger proposals; constitution human-only; source text wrapped as data; TQ sources may not create new notes, only attach as evidence; diffs over N lines or touching `stakes: high` notes require approval | Diff-size histogram; lint rule "claim without anchor"; canary note that must never change |
| C5 | Consolidator | Concurrent edit with you in Obsidian | High | Lost human edits | Optimistic concurrency: consolidator records each target file's hash at read, writes only if unchanged, otherwise re-plans next cycle. Human edits always win; `locked: true` opts a note out entirely | Conflict-retry counter |
| C6 | Consolidator | Drift / homogenization over many rewrites | Med | Nuance lost; false consensus | Proposes diffs, not rewrites; each sentence keeps its anchor; periodic "diff vs. raw" spot check by the linter | % notes whose claims all still resolve to raw anchors |
| C7 | Consolidator | Hallucinated links / misattributed claims | High | Beliefs from nothing | Citation-entailment check; unanchored claims capped at T3 and flagged | Entailment-failure rate |
| C8 | LLM-set probabilities | Uncalibrated `p` | High | Surprise ranking and digest become noise | Human confirmation at band crossings; separate Brier score for LLM proposals; surprise counted only for claims with cap ≥ T2 | LLM-vs-human Brier gap |
| C9 | Index | Embedding model changed (dimension or semantics) | Med | Vector search returns garbage | `meta` table stores `embed_model` + dim; mismatch blocks vector stage and triggers background re-embed; FTS keeps working | Model-id mismatch check at startup |
| C10 | Index | `index.db` corrupt, or synced across devices | Med | Search down | **Never sync the DB**; each device rebuilds locally; `PRAGMA integrity_check` on start → auto-rebuild | Integrity check result |
| C11 | Retrieval | PPR latency on a large graph | Low | Slow answers | PPR on a subgraph (k-hop around seeds); cache per seed set; skip stage 3 when latency budget exceeded | p95 query latency per stage |
| C12 | Retrieval | Graph stage hurts single-hop factual recall | Med | Worse answers than plain RAG | Graph results are **fused**, never substituted; eval gate on the single-hop category | Per-category recall in eval harness |
| C13 | Edit-on-access lint | Read amplification / feedback loop (linter reads → triggers lint) | Med | Runaway LLM cost | On-access lint is queued, deduped per note per 24h, and the linter's own reads never enqueue | Lint jobs/day vs. notes read/day |
| C14 | Belief ledger | Low-tier flood pushes beliefs down ("make you forget") | High | Good beliefs eroded | Downgrades from T3/TQ are proposals → ACH stub + review queue | Pending downgrade proposals |
| C15 | Belief ledger | Clock skew / time-zone errors in bi-temporal fields | Low | Wrong "as of" answers | Store UTC ISO-8601 only; valid-time may be a date or interval with explicit precision | Lint: valid_from > tx_from + slack |
| C16 | Retention | Review backlog explodes after time away | Med | User abandons review | Daily cap; FSRS ranks by lowest retrievability × note importance; "vacation mode" shifts due dates | Overdue count; days since last review |
| C17 | Inbox | Automated harvesters → hoarding | Med | Signal drowned | Scent floor + expiry; capture→distill ratio on the dashboard | Ratio < 0.3 for 2 weeks |
| C18 | Privacy | Sensitive notes sent to a cloud LLM | Critical | Data leaves the machine | Per-note `trust_level`; `classified`+ routes only to local providers (enforced in `LLMRouter`, not by convention) | Router audit log: cloud calls carrying classified IDs (must be 0) |
| C19 | Privacy | Need to truly delete something (append-only + Git history) | Med | Can't forget | `purge` op: tombstone in ledger + `git filter-repo` on the path + rebuild; for routinely sensitive material, encrypt per-note and shred the key | Purge log |
| C20 | Tooling | Obsidian/plugin/skill format changes | Low | Client breaks | Plain Markdown + YAML only; no plugin-specific syntax in core fields | Round-trip test Markdown → index → Markdown |

### 12.3 Cascade diagram

```
[LLM provider down / budget exhausted]
  ├── Capture (raw + FTS)        — unaffected (no LLM on the hot path)
  ├── Lexical search             — unaffected
  ├── Episodes / contextual chunks / embeddings — queued (degraded freshness)
  ├── Consolidation / lint       — paused; backlog grows
  │   └── Surprise digest        — stale, labelled with last-run time
  └── Review (FSRS)              — unaffected (local, deterministic)

[index.db lost or corrupt]
  ├── Search (all stages)        — down until rebuild
  └── Truth (raw, wiki, ledger, reviews) — unaffected → `anamnesis rebuild`

[Poisoned source ingested]
  ├── New notes                  — blocked if TQ; T3 notes flagged "unanchored/low tier"
  ├── Belief increases           — capped by best independent chain
  ├── Belief decreases           — held as proposals → ACH + review
  └── Constitution               — unreachable (human-only write)
```

### 12.4 Rollback checklist

- [ ] **Kill switches** in `constitution.md`: `consolidation: off`, `edit_on_access: off`, `cloud_llm: off`. Each disables one loop without touching data.
- [ ] **Undo a consolidation run:** each run is one Git commit tagged `consolidate/<run-id>` → `git revert` it; the ledger lines for that run carry `run_id` and get a compensating `superseded_by: revert/<run-id>` entry.
- [ ] **Index:** delete `index.db` → `anamnesis rebuild`.
- [ ] **Bad embedding model:** revert `embed_model` in meta → re-embed.
- [ ] **Leak or poisoning incident:** quarantine the source (set to TQ), run `anamnesis blast-radius <raw-sha>` (every claim and note downstream via provenance), review or revert.

### 12.5 Other non-failure risks

| Risk | Mitigation |
|---|---|
| Over-engineering | Everything above L2 is optional per note; default flow is capture → auto-consolidate → review. *Wu wei*: no field is mandatory for a human |
| Benchmark theater | Only the personal eval harness (§9) gates changes |
| Secondary-source numbers in this doc | Marked inline; re-verify against primaries before quoting them elsewhere |
| Lock-in | Markdown + YAML + JSONL + Git; the DB is disposable |

---

## 13. Build plan

| Phase | Deliverable | Exit criterion |
|---|---|---|
| **P0 Skeleton** (1–2 days) | Folder layout, `constitution.md`, `facets.yaml`, `index.md`/`log.md` conventions; `qmd` as interim search; the §10.1 skill pack (capture / ingest / query / lint first) | You can capture, ingest, and query 20 real sources |
| **P1 Index** | `IndexerNode`: contextual chunking, FTS5, sqlite-vec, RRF, rerank; `anamnesis rebuild` | Personal eval harness v0 (50 Qs) running; recall@10 baseline recorded |
| **P2 Episodic + sleep** | `InboxNode`, `EpisodeNode`, `ConsolidatorNode` with A-MEM evolution; edit-on-access lint | Nightly consolidation produces reviewed diffs; orphan rate < 10% |
| **P3 Graph** | Bi-temporal edges in `KnowledgeGraphNode`; PPR stage; `as_of` queries | Multi-hop and temporal eval categories improve over P1 |
| **P4 Beliefs** | `BeliefNode`, trust tiers, ICD-203 bands, surprise digest, ACH and prediction notes, Brier dashboard | Poisoning property test passes; first calibration curve |
| **P5 Retention** | `ResurfaceNode` with py-fsrs for prompts/notes/beliefs; serendipity card; weekly review | Daily review ≤ 5 min; first month of retention data |
| **P6 Clients** | MCP server; Obsidian-compatible vault; `pi` commands | Usable from Claude Code, Obsidian, and CLI interchangeably |

---

## 14. Sources

Primary or near-primary where available; secondary sources noted.

**Memory systems & retrieval**
- Karpathy, *llm-wiki* (idea file, 2026-04-04): https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f
- Gutiérrez et al., *From RAG to Memory: Non-Parametric Continual Learning for LLMs* (HippoRAG 2): https://arxiv.org/html/2502.14802v1 · code: https://github.com/OSU-NLP-Group/HippoRAG
- Rasmussen et al., *Zep: A Temporal Knowledge Graph Architecture for Agent Memory*: https://blog.getzep.com/content/files/2025/01/ZEP__USING_KNOWLEDGE_GRAPHS_TO_POWER_LLM_AGENT_MEMORY_2025011700.pdf · Graphiti overview: https://neo4j.com/blog/developer/graphiti-knowledge-graph-memory/
- Xu et al., *A-MEM: Agentic Memory for LLM Agents*: https://arxiv.org/html/2502.12110v9
- Zhou & Han, *A Simple Yet Strong Baseline for Long-Term Conversational Memory*: https://arxiv.org/pdf/2511.17208
- Mastra, *Observational Memory* (vendor): https://mastra.ai/research/observational-memory · coverage: https://the-decoder.com/mastras-open-source-ai-memory-uses-traffic-light-emojis-for-more-efficient-compression/ · https://venturebeat.com/ai/observational-memory-cuts-ai-agent-costs-10x-and-outscores-rag-on-long
- Letta, sleep-time agents: https://docs.letta.com/guides/agents/architectures/sleeptime · https://letta.com/blog/sleep-time-compute
- Anthropic, Contextual Retrieval (secondary summaries): https://www.maginative.com/article/anthropics-contextual-retrieval-technique-enhances-rag-accuracy-by-67 · https://gradientflow.com/contextual-retrieval-rag/
- Willison, *Hybrid full-text search and vector search with SQLite*: https://simonwillison.net/2024/Oct/4/hybrid-full-text-search-and-vector-search-with-sqlite/
- Basic Memory docs: https://docs.basicmemory.com/technical/technical-information
- Cognee (secondary): https://dev.to/andrew-ooo/cognee-review-open-source-ai-memory-for-agents-3a1e
- qmd (secondary): https://korben.info/en/qmd-local-search-engine-markdown-notes.html
- RAPTOR / GraphRAG / LightRAG comparisons: https://arxiv.org/pdf/2505.24226 · https://arxiv.org/pdf/2506.05690
- Benchmark caveats: https://mem0.ai/blog/ai-memory-benchmarks-in-2026 (vendor) · https://dev.to/everest_an/-i-benchmarked-ai-agent-memory-in-2026-and-the-numbers-tell-a-different-story-than-the-marketing-2ae4 · https://arxiv.org/pdf/2608.29606
- Memory poisoning & provenance: https://arxiv.org/pdf/2606.22030 · https://www.alphaxiv.org/abs/2605.14421 · https://blog.getzep.com/defending-agent-memory-poisoning.md
- Embedding models: https://huggingface.co/blog/embeddinggemma · https://d-central.tech/local-embedding-models/ (secondary)
- Awesome list: https://github.com/Mindola-ai/awesome-second-brain · https://github.com/AgriciDaniel/claude-obsidian · https://github.com/Astro-Han/karpathy-llm-wiki

**Learning & cognitive science**
- Kumaran, Hassabis & McClelland, *What Learning Systems do Intelligent Agents Need? CLS Theory Updated*: https://web.stanford.edu/~jlmcc/papers/KumaranHassabisMcClelland16FinalMS.pdf
- Bjork & Bjork, *Making things hard on yourself, but in a good way* (2011): https://bjorklab.psych.ucla.edu/wp-content/uploads/sites/13/2016/04/EBjork_RBjork_2011.pdf
- FSRS: https://borretti.me/article/implementing-fsrs-in-100-lines · https://hackage-content.haskell.org/package/haskell-fsrs
- Matuschak & Nielsen, Quantum Country notes: https://notes.andymatuschak.org/zHMuXkWrGpDVQ6j82YNK2iD · https://notes.andymatuschak.org/z2Rv4RAjXUrwa3fMYn9Q1iR
- Reconsolidation review: https://pmc.ncbi.nlm.nih.gov/articles/PMC5605913/
- Itti & Baldi, *Bayesian Surprise Attracts Human Attention*: https://proceedings.neurips.cc/paper_files/paper/2005/file/0172d289da48c48de8c5ebf3de9f7ee1-Paper.pdf

**Sensemaking, intelligence & forecasting**
- Pirolli & Card sensemaking (secondary): https://vislab-ccom.unh.edu/VTDP_web_pages/VTDP_Sensemaking.html · https://arxiv.org/pdf/2204.04758
- Information foraging: https://en.wikipedia.org/wiki/Information_foraging · https://chi1997.acm.org/proceedings/paper/plp.html
- ACH: https://en.wikipedia.org/wiki/Analysis_of_competing_hypotheses · NATO SAS-114 test: https://www.sto.nato.int/publications/STO%20Meeting%20Proceedings/STO-MP-SAS-114/MP-SAS-114-ANN-G.pdf
- ICD 203 estimative language (secondary; verify against dni.gov): https://eugit.opencloud.lu/MISP/misp-taxonomies/commit/26ac124fe96a6cee9001c9c332cb9cc7328e7f09 · ODNI on confidence levels: https://www.dni.gov/files/documents/FOIA/DF-2024-00001-NIU_Research_Short.pdf
- Superforecasting (secondary): https://www.econlib.org/archives/2015/10/superforecastin.html

**Knowledge organization & PKM practice**
- Matuschak, *Evergreen notes*: https://notes.andymatuschak.org/Evergreen_notes
- Luhmann, *Communicating with Slip Boxes*: https://zettelkasten.de/communications-with-zettelkastens/
- Forte, *Building a Second Brain* (secondary): https://mmapped.blog/posts/16-building-a-second-brain
- Collector's Fallacy: https://concepts.dsebastien.net/concept/collectors-fallacy/
- GTD / Zeigarnik (secondary): https://super-productivity.com/blog/zeigarnik-effect-productivity/
- Faceted classification: https://en.wikipedia.org/wiki/Faceted_classification
- Xanadu / transclusion: https://concepts.dsebastien.net/concept/project-xanadu/ · https://www.notion.com/blog/designing-synced-blocks
- W3C annotation anchoring: https://web.hypothes.is/blog/fuzzy-anchoring/ · https://annotator.apache.org/docs/api/modules/dom.html
- Ink & Switch, *Local-first software*: https://inkandswitch.com/local-first/
- Datomic data model: https://docs.datomic.com/data-model.html
