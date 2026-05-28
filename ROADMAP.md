# probable-intel Roadmap

Each item is self-contained: what to build, why, pre-made decisions, files to touch, how to verify.
Run `/sprint` to pick up and fully execute the next unchecked item.

---

## In Progress

- [ ] **Reliability hardening — complete**
  Finish wiring circuit breaker into all harvesters; fix lifecycle concurrent-restart race; fix ChannelMetrics.dropped counting; rate-limit drop warning logs.
  Files: `base.py`, `feed_node.py`, `api_node.py`, `social_node.py`, `web_node.py`, `recon_node.py`, `spine/spine.py`, `hub/lifecycle.py`
  Decision: circuit breaker lives on BaseNode (_cb_check/_cb_success/_cb_failure) — all harvesters inherit it, none re-implement their own
  Decision: lifecycle.restart() uses `_restarting: set[str]` guard — not a lock, not asyncio.Lock (this is single-threaded asyncio)
  Decision: drop warning rate-limited to once per 60s per channel via `_last_drop_log` dict; always increment `ch.metrics.dropped` for observability
  Verify: `python -m pytest tests/ -q` (110+ tests pass)

---

## Sprint Queue (ordered by value/complexity)

- [ ] **Redis Spine adapter**
  Replace asyncio.Queue with Redis Streams for multi-process scale-out. Enables running harvesters and analysts in separate processes.
  Files: `probable_intel/spine/redis_spine.py` (new), `probable_intel/nexus/spec.py` (add SpineSpec), `probable_intel/nexus/parser.py` (parse `spine:` block)
  Decision: `RedisSpine` implements the same interface as `Spine` (publish/subscribe/channel_metrics/all_channel_names). `SpineSubscription` wraps a Redis consumer group read. The `Spine` class is never modified — callers can't tell which backend they're using.
  Decision: use `redis.asyncio` (bundled in redis-py ≥4.2). No aioredis.
  Decision: channel names map directly to Redis Stream keys prefixed with `pi:`.
  Config: `spine: {backend: redis, url: "redis://localhost:6379"}` in apparatus file.
  Why: horizontal scaling and process isolation without changing any node code.
  Verify: `python -m pytest tests/unit/test_spine.py -v` + integration test with mock Redis

- [ ] **Semantic memory layer — vector search over stored packets**
  Add similarity retrieval so operators can query "find threats similar to this CVE description". Built on top of StorageNode.
  Files: `probable_intel/storage/vector_backend.py` (new), `probable_intel/nodes/archivists/storage_node.py` (add vector indexing), `probable_intel/cli/main.py` (add `pi search` command)
  Decision: use `sentence-transformers` with `all-MiniLM-L6-v2` (22MB, no API key, runs locally). Store embeddings in SQLite-vec or as BLOB columns with cosine sim computed in Python (fallback if sqlite-vec unavailable).
  Decision: index only `payload.content` field, truncated to 512 tokens.
  Decision: `pi search "ransomware C2 infrastructure" --top-k 10` — CLI command, not a new node.
  Why: enables retrospective analysis without re-running the pipeline.
  Verify: `python -m pytest tests/unit/nodes/test_storage_node.py -v` + manual `pi search` smoke test

- [ ] **MITRE ATT&CK mapping + STIX 2.1 export**
  Map threat labels to ATT&CK technique IDs; export collected intelligence as STIX 2.1 bundles.
  Files: `probable_intel/nodes/analysts/threat_node.py` (add ATT&CK mapping), `probable_intel/cli/main.py` (add `pi export --format stix` command), `nexus/data/attack_mapping.yaml` (new static lookup)
  Decision: ATT&CK mapping is a static YAML file in `nexus/data/` — not a live API call. Updated manually when ATT&CK releases new versions.
  Decision: STIX export uses `stix2` library. Bundle per apparatus run (not per packet).
  Decision: only HIGH/CRITICAL threats are exported; LOW/MEDIUM are filtered.
  Why: enables interoperability with SOC platforms (Splunk, IBM QRadar, OpenCTI).
  Verify: `pi export nexus/apparatuses/mvp-demo.nx --format stix --output /tmp/test.json` + validate with stix2-validator

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

- [x] Core infrastructure — Spine, IntelPacket, BaseNode, NEXUS DSL parser/validator/loader, Hub, NodeRegistry, lifecycle, HealthMonitor, NodeFactory, HubAPI, CLI (`pi validate/run/watch/status`)
- [x] Harvester nodes — FeedNode (RSS/Atom), WebNode (httpx scraper), ApiNode (REST + presets: NVD, CIRCL, GreyNoise), SocialNode (Reddit/HackerNews/Mastodon), ReconNode (autonomous OSINT via Google News RSS)
- [x] Analyst nodes — SentimentNode (VADER + LLM fallback), EntityExtractorNode (spaCy NER + LLM fallback), ThreatAssessNode (rule DSL eval), NarrativeNode (LLM rolling-window synthesis)
- [x] Sentinel nodes — AnomalyNode (z-score anomaly detection), AlertNode (webhook + logfile)
- [x] Archivist nodes — StorageNode (SQLite, dedup via INSERT OR IGNORE), KnowledgeGraphNode (NetworkX entity co-occurrence graph)
- [x] Coordinator — TaskRouterNode (OODA loop, LLM-enhanced collection directives)
- [x] CounterIntel — OpSecNode (request-pattern auditing), DeceptionNode (honeypots + canaries), FingerprintDefenseNode (browser identity injection), AttributionNode (passive adversary profiling)
- [x] Provider-agnostic LLM layer — LLMProvider ABC, AnthropicProvider, OllamaProvider, VLLMProvider, LLMRouter (budget guard + factory)
- [x] Federation — FederatedSpine (multi-hub HTTP push/SSE pull), /federate/* HubAPI endpoints, FederationSpec parser
- [x] CI/CD — GitHub Actions (Python 3.11/3.12 matrix): install → validate → unit tests → integration tests
- [x] Proxy support — `trust_env=True` on all harvesters; `HTTPS_PROXY`/`HTTP_PROXY`/`ALL_PROXY` env vars respected
- [x] Elegance refactor — `_wait_any()` and emit init extracted to BaseNode (removed 199 lines of boilerplate across 16 files)
- [x] Reliability hardening (partial) — circuit breaker in BaseNode, wired to FeedNode + ApiNode; HealthMonitor max-restart cap; `_wait_any` CancelledError fix; Channel.get() CancelledError fix
