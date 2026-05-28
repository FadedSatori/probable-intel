# ADR-004: VADER as primary sentiment, LLM as fallback

**Status:** Accepted

## Context

SentimentNode needs to score text sentiment (-1.0 to +1.0). Options: pure LLM, pure VADER, VADER with LLM fallback on low-confidence inputs.

## Decision

VADER is the primary scorer. LLM is called only when VADER's compound confidence is below `llm_threshold` (default 0.0, meaning any non-neutral score avoids LLM). LLM requires `spec.llm` to be set; if not set, VADER is used unconditionally.

## Rationale

- **Cost** — VADER is free and instant; LLM costs money per call. Running LLM on every packet would exhaust budget quickly.
- **Throughput** — VADER scores in microseconds; LLM adds 500ms–2s latency per packet
- **Quality** — for short cybersecurity text (CVE descriptions, news headlines), VADER is surprisingly accurate. LLM adds value mainly on ambiguous or nuanced text.
- **Availability** — VADER works offline; LLM requires API key and network
- **Budget guard** — `LLMRouter` enforces `budget_per_day_usd`; the VADER-first approach naturally minimizes LLM calls

## Consequences

- VADER accuracy degrades on long-form text (articles > 200 words) — acceptable since content is chunked at ingestion
- LLM fallback path adds latency spikes when triggered — operators should set `llm_threshold` conservatively
- Sentiment scores from VADER and LLM are on the same -1.0/+1.0 scale; no normalization needed
