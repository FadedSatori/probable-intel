# ADR-001: asyncio over threading for node concurrency

**Status:** Accepted

## Context

The system runs many nodes concurrently (18+ in the MVP apparatus). Each node blocks on I/O: HTTP fetches, queue reads, sleep intervals. We needed a concurrency model.

## Decision

Use Python `asyncio` throughout. All nodes are coroutines. The Spine is `asyncio.Queue`-based. The Hub runs a single event loop.

## Rationale

- **No shared-state races** — single-threaded event loop eliminates data races on node state without locks
- **I/O-bound workload** — all blocking is I/O (network, DB, queue wait), not CPU. asyncio is ideal; threading adds overhead for no gain.
- **Cancellation** — `asyncio.Task.cancel()` + `CancelledError` gives clean shutdown; threading has no equivalent
- **Ecosystem** — `httpx`, `aiosqlite`, `fastapi` are all async-native; mixing sync and async would require `run_in_executor` everywhere
- **Test simplicity** — `pytest-asyncio` makes async unit tests straightforward

## Consequences

- CPU-bound work (spaCy NER, embedding inference) blocks the loop. Mitigation: offload to `loop.run_in_executor()` when added.
- Multi-process scale-out requires Redis Spine adapter (see ROADMAP). The asyncio Spine is single-process only.
- `asyncio.CancelledError` must propagate cleanly — never swallow it in a bare `except Exception`.
