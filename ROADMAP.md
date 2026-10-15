# probable-intel Roadmap

Each item is self-contained: what to build, why, pre-made decisions, files to touch, how to verify.
Run `/sprint` to pick up and fully execute the next unchecked item.

---

## In Progress

- [ ] **MITRE ATT&CK mapping + STIX 2.1 export**
  Map threat labels to ATT&CK technique IDs; export collected intelligence as STIX 2.1 bundles.
  Files: `probable_intel/nodes/analysts/threat_node.py` (add ATT&CK mapping), `probable_intel/cli/main.py` (add `pi export --format stix` command), `nexus/data/attack_mapping.yaml` (new static lookup)
  Decision: ATT&CK mapping is a static YAML file in `nexus/data/` — not a live API call. Updated manually when ATT&CK releases new versions.
  Decision: STIX export uses `stix2` library. Bundle per apparatus run (not per packet).
  Decision: only HIGH/CRITICAL threats are exported; LOW/MEDIUM are filtered.
  Why: enables interoperability with SOC platforms (Splunk, IBM QRadar, OpenCTI).
  Verify: `pi export nexus/apparatuses/mvp-demo.nx --format stix --output /tmp/test.json` + validate with stix2-validator

---

## Sprint Queue (ordered by value/complexity)

- [ ] **Textual TUI dashboard**
  Live terminal dashboard showing packet flow, channel depths, node health, and recent alerts.
  Files: `probable_intel/cli/dashboard.py` (new), `pyproject.toml` (add `textual>=0.60` to optional `[tui]` group), `probable_intel/cli/main.py` (add `pi dashboard` command)
  Decision: single-file Textual app. Subscribes to all Spine channels after Hub starts.
  Decision: layout: left panel = node health table (id, state, error_count, last_heartbeat); right panel = live packet stream colored by priority; bottom = channel depth bars.
  Decision: refresh every 2s via `set_interval`. No websocket — same process as hub.
  Why: operators need visibility without writing custom subscribers.
  Verify: `pi dashboard nexus/apparatuses/mvp-demo.nx` renders without crash (visual check)

- [ ] **DarkWebNode — Tor-based OSINT collection**
  Harvester node that collects from .onion sources via Tor SOCKS5 proxy.
  Files: `probable_intel/nodes/harvesters/darkweb_node.py` (new), `pyproject.toml` (add `requests[socks]` to optional `[browser]` group)
  Decision: use `httpx` with SOCKS5 transport (`httpx.AsyncHTTPTransport(proxy="socks5://127.0.0.1:9050")`). Does NOT bundle Tor — operator must run `tor` separately.
  Decision: circuit breaker inherited from BaseNode. No special Tor circuit management.
  Decision: emits `RawDarkWebPacket`. Trust level defaults to UNCLASSIFIED (operator can override to RESTRICTED in apparatus config).
  Decision: only fetch .onion URLs — validator rejects non-.onion targets for this node type.
  Prerequisite: `tor` daemon running on operator's host; `HTTPS_PROXY=socks5h://127.0.0.1:9050` env var set.
  Verify: `pi validate` passes for apparatus using DarkWebNode; mock SOCKS test passes

- [ ] **Neo4j backend for KnowledgeGraphNode**
  Swap NetworkX in-memory graph for persistent Neo4j. Enables graph queries that survive restarts.
  Files: `probable_intel/graph/neo4j_engine.py` (new), `probable_intel/nodes/archivists/kg_node.py` (add backend dispatch on `config.backend`)
  Decision: `backend: neo4j` in node config activates Neo4j. Default remains `networkx` (no breaking change).
  Decision: use `neo4j` driver (official, async). Connection params from apparatus config: `uri`, `auth_env` (env var for password).
  Decision: NetworkX engine and Neo4j engine expose identical interface: `add_node(key, attrs)`, `add_edge(src, dst, weight)`, `neighbors(key)`, `top_connected(n)`, `path(src, dst)`.
  Why: in-memory NetworkX loses graph on restart; Neo4j persists and scales.
  Verify: `python -m pytest tests/unit/nodes/test_kg_node.py -v` (existing tests pass with NetworkX backend unchanged)

---

## Completed

- [x] **Semantic memory layer** — `storage/vector_backend.py`: StorageNode embeds each article's `payload.content` once per `source_hash` (all-MiniLM-L6-v2 via sentence-transformers, 512-token cap, model load + encode in a thread executor) into a `packet_vectors` table in its SQLite file; `pi search "…" --top-k 10` ranks by cosine similarity in SQLite via sqlite-vec, else in Python. Optional `[semantic]` extra; config `vector_index: false` opts out. Tested with a deterministic stand-in embedder, incl. the full mvp-demo pipeline.
- [x] **Redis Spine adapter** — `RedisSpine` (`probable_intel/spine/redis_spine.py`) behind the Spine interface; `spine: {backend: redis, url}` in an apparatus selects it. One private consumer group per subscription (same fan-out as the in-process Spine); a background reader per subscription feeds a local queue so `get()` stays cancel-safe (redis-py can swallow a cancellation mid-command). Full mvp-demo pipeline passes on memory, fakeredis and real Redis.
- [x] **Reliability hardening — complete**
  Wired circuit breaker into all harvesters; fixed lifecycle concurrent-restart race; fixed ChannelMetrics.dropped counting; rate-limited drop warning logs.
  Status: All 5 harvesters (FeedNode, ApiNode, SocialNode, WebNode, ReconNode) now inherit and use circuit breaker from BaseNode. Lifecycle restart guard prevents concurrent restart races. Spine metrics accurately track queue drops with rate-limited warnings.

- [x] Core infrastructure — Spine, IntelPacket, BaseNode, NEXUS DSL parser/validator/loader, Hub, NodeRegistry, lifecycle, HealthMonitor, NodeFactory, HubAPI, CLI (`pi validate/run/watch/status`)
- [x] Harvester nodes — FeedNode (RSS/Atom), WebNode (httpx scraper), ApiNode (REST + presets: NVD, CIRCL, GreyNoise), SocialNode (Reddit/HackerNews/Mastodon), ReconNode (autonomous OSINT via Google News RSS)
- [x] Analyst nodes — SentimentNode (VADER + LLM fallback), EntityExtractorNode (spaCy NER + LLM fallback), ThreatAssessNode (rule DSL eval), NarrativeNode (LLM rolling-window synthesis)
- [x] Sentinel nodes — AnomalyNode (z-score anomaly detection), AlertNode (webhook + logfile)
- [x] Archivist nodes — StorageNode (SQLite, dedup via INSERT OR IGNORE), KnowledgeGraphNode (NetworkX entity co-occurrence graph)
- [x] Coordinator — TaskRouterNode (OODA loop, LLM-enhanced collection directives)
- [x] CounterIntel — OpSecNode (request-pattern auditing), DeceptionNode (honeypots + canaries), FingerprintDefenseNode (per-request jitter + request audit), AttributionNode (passive adversary profiling)
- [x] Provider-agnostic LLM layer — LLMProvider ABC, AnthropicProvider, OllamaProvider, VLLMProvider, LLMRouter (budget guard + factory)
- [x] Federation — FederatedSpine (multi-hub HTTP push/SSE pull), /federate/* HubAPI endpoints, FederationSpec parser
- [x] CI/CD — GitHub Actions (Python 3.11/3.12 matrix): install → validate → unit tests → integration tests
- [x] Proxy support — `trust_env=True` on all harvesters; `HTTPS_PROXY`/`HTTP_PROXY`/`ALL_PROXY` env vars respected
- [x] Elegance refactor — `_wait_any()` and emit init extracted to BaseNode (removed 199 lines of boilerplate across 16 files)
- [x] Reliability hardening (partial) — circuit breaker in BaseNode, wired to FeedNode + ApiNode; HealthMonitor max-restart cap; `_wait_any` CancelledError fix; Channel.get() CancelledError fix
- [x] CI restored — suite had hung since May (stale Reddit test awaiting a packet from a deprecated no-op); fixed `_handle_fetch_error` TypeError; reverted StorageNode batching (no I/O win: `save()` commits per row); `pytest-timeout` (60s/test) + 15-min job cap so hangs fail fast
- [x] Pipeline shakedown — full mvp-demo apparatus (14 nodes) runs end-to-end offline in CI (`tests/integration/test_mvp_demo_pipeline.py`). Fixed: nested `config:` blocks ignored; apparatus storage path ignored; `_wait_any` dropped packets on multi-channel nodes; AlertNode never published; TaskRouter wildcard directives were no-ops; Hub missed early directives and leaked nodes on setup failure; StorageNode lost startup packets
- [x] Node test backfill — every node type now has unit tests (Feed, Web, EntityExtractor, ThreatAssess, OpSec, Deception, Fingerprint added). Fixed: RSS packets had empty url, Atom fields were object reprs, Recon dropped article bodies; rule parser misread operators inside values; unvalidated LLM entities; honeypot/opsec events never alerted; OpSec reuse detection capped at 200; FingerprintDefense jitter config unvalidated
- [x] FingerprintDefenseNode wired — Hub hands each apparatus's FingerprintDefenseNode to its nodes; all 5 harvesters call `BaseNode._before_request()` before every outbound request (jitter + audit to OpSecNode on `ci.opsec.audit`). Added to mvp-demo and cyber-collection.
