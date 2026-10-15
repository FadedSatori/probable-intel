# ADR-005: source_hash for deduplication, not packet_id

**Status:** Accepted

## Context

The same content can enter the pipeline multiple times: a CVE from NVD also appears in a news feed; the same article is fetched on two consecutive poll cycles. We needed a deduplication key.

## Decision

`IntelPacket.source_hash` is a SHA-256 hex digest (truncated to 16 chars) of the content at its origin. It is set once when the packet is created and preserved through all `relay()` hops. `StorageNode` uses `INSERT OR IGNORE` keyed on `source_hash`. `packet_id` (UUID) is for tracing/logging only.

## Rationale

- **Content-addressed** — two packets created from the same source URL or content body get the same hash, regardless of when they were created or which node created them
- **Stable across relay** — `packet.relay(new_node, new_channel)` copies `source_hash` unchanged. A packet seen at `SentimentNode` and again at `StorageNode` is recognizable as the same item.
- **StorageNode idempotency** — `INSERT OR IGNORE` means duplicate ingestion is a no-op, not an error. This is the correct semantic for an intelligence accumulator.
- **FeedNode dedup** — each harvester maintains a local `_seen_hashes` set (in-memory) keyed on `source_hash` to avoid re-emitting items within the same process lifetime. StorageNode's DB dedup catches cross-session duplicates.

## Consequences

- `packet_id` must never be used for deduplication — it changes on every packet creation
- When calling `relay()`, do not set a new `source_hash` — it would break dedup across the pipeline
- Extremely similar but non-identical content gets different hashes — this is intentional (an updated CVE description is new content)
- Hash truncation to 16 chars gives 64 bits of collision resistance — sufficient for the expected volume (millions, not billions, of unique items)
