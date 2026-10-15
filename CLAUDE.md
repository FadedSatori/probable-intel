# probable-intel — CLAUDE.md

Developer reference for working on this codebase with Claude Code.

## Build & test commands

```bash
# Install (editable, with dev extras)
pip install -e ".[dev]"
pip install atoma vaderSentiment aiosqlite httpx pyyaml networkx respx   # runtime + test deps

# Optional extras
pip install -e ".[nlp]"      # spaCy (EntityExtractorNode full mode)
pip install -e ".[browser]"  # Playwright + curl_cffi (WebNode, FingerprintDefenseNode)
pip install -e ".[redis]"    # redis-py (RedisSpine — multi-process Spine over Redis Streams)
pip install -e ".[semantic]" # sentence-transformers + sqlite-vec (semantic memory, `pi search`)
pip install -e ".[stix]"     # stix2 (STIX 2.1 export, `pi export`)

# Run all tests
python -m pytest

# Run by tier
python -m pytest tests/unit/
python -m pytest tests/integration/

# Spine tests run on fakeredis; add a real Redis with:
REDIS_URL=redis://127.0.0.1:6379/0 python -m pytest

# Lint / type check
ruff check .
mypy probable_intel/

# CLI
pi validate nexus/apparatuses/mvp-demo.nx
pi run      nexus/apparatuses/mvp-demo.nx
pi status   nexus/apparatuses/mvp-demo.nx
pi watch    nexus/apparatuses/mvp-demo.nx [--channel raw.feed.security]
pi search   "ransomware C2 infrastructure" --top-k 10 [--db /path/to/main.db]
pi export   nexus/apparatuses/mvp-demo.nx --format stix --output bundle.json [--since ISO-8601]
```

## Project layout

```
probable_intel/
├── spine/          # IntelPacket, Spine (in-process bus), RedisSpine (Redis Streams), Channel
├── nexus/          # NEXUS DSL — parser, validator, loader, spec dataclasses, errors
├── hub/            # Hub orchestrator, NodeRegistry, lifecycle, health, NodeFactory, API,
│                   # federation.py (FederatedSpine — multi-hub HTTP push/SSE pull)
├── llm/            # Provider-agnostic LLM layer
│   ├── base.py              # LLMProvider ABC + LLMError
│   ├── anthropic_provider.py
│   ├── ollama_provider.py   # Ollama local models
│   ├── vllm_provider.py     # vLLM / any OpenAI-compat endpoint
│   └── router.py            # LLMRouter — budget guard + provider factory
├── nodes/
│   ├── base.py                     # BaseNode — state machine, heartbeat, run loop, pause support
│   ├── harvesters/                 # FeedNode, WebNode, ApiNode, SocialNode
│   ├── analysts/                   # SentimentNode, EntityExtractorNode, ThreatAssessNode,
│   │                               #   NarrativeNode (LLM-powered synthesis)
│   ├── sentinels/                  # AlertNode, AnomalyNode
│   ├── archivists/                 # StorageNode, KnowledgeGraphNode
│   ├── coordinators/               # TaskRouterNode (OODA loop)
│   └── counterintel/               # OpSecNode, DeceptionNode, FingerprintDefenseNode,
│                                   #   AttributionNode (passive adversary profiling)
├── storage/        # SQLiteBackend (async, INSERT OR IGNORE); vector_backend (semantic memory)
├── export/         # stix_export — STIX 2.1 bundles of stored HIGH/CRITICAL threats
└── cli/            # Typer app — validate / run / status / watch / search / export

nexus/
├── apparatuses/    # Operator .nx files (one per mission)
└── data/           # attack_mapping.yaml — static MITRE ATT&CK lookup for ThreatAssessNode

tests/
├── unit/           # test_nexus_parser, test_spine, test_threat_eval, nodes/
└── integration/    # test_apparatus_e2e (full-pipeline tests)
```

## Architecture

```
.nx file → NexusParser → ApparatusSpec
                              ↓
                         ApparatusValidator  (topology checks: cycles, orphan channels, LLM placement)
                              ↓
Hub.load_apparatus()
  ├── NodeFactory  (NodeSpec → Python class instance)
  ├── NodeRegistry (id → node + state)
  ├── NodeLifecycleManager  (DECLARED → INITIALIZING → IDLE → RUNNING → ERROR/STOPPED)
  ├── HealthMonitor (heartbeat every 15s; threshold = 3× interval)
  ├── FederatedSpine (optional — wires hub to peer hubs over HTTP)
  └── directive_loop (applies TaskDirectivePackets from system.task.directives)
                              ↓
                    Spine (named channels; one FIFO queue per subscriber)
                              ↓
                    Nodes communicate only via IntelPacket on channels
```

**Spine** — `spine.publish(channel, packet)` fans out to all subscribers. Each subscription has its own FIFO `asyncio.Queue` (5,000 cap; overflow is counted in `channel_metrics(ch).dropped`). `IntelPacket.priority` travels with the packet but does not reorder delivery. `RedisSpine` provides the same interface over Redis Streams.

**IntelPacket** — all inter-node data. Key fields: `packet_type`, `source_node_id`, `apparatus_id`, `channel`, `payload: dict`, `priority: Priority`, `trust_level: TrustLevel`, `provenance: list[str]`, `source_hash` (SHA-256, for dedup), `ttl_seconds`. Use `packet.relay(new_node_id, new_channel, ...)` to forward — preserves `source_hash` and extends provenance.

**BaseNode** — subclass and implement `setup()`, `run()`, `teardown()`. The base class handles the run loop (calls `run()` repeatedly), heartbeat emission, exponential backoff on errors, and `_paused_until` support (set by Hub directives). `run()` should block on one unit of work (e.g. wait for one packet), not loop forever.

**LLMRouter** — provider-agnostic. Select via `spec.llm.provider`:
- `anthropic` — Anthropic Claude API (requires `ANTHROPIC_API_KEY`)
- `ollama` — Ollama local model server (no key; set `base_url`)
- `vllm` — vLLM or any OpenAI-compatible endpoint (set `base_url`; optional key)

## Adding a new node type

1. Create `probable_intel/nodes/<tier>/<name>_node.py` subclassing `BaseNode`
2. Implement `setup()`, `run()`, `teardown()`
3. Register in `probable_intel/hub/factory.py` → `_lazy_imports()` dict
4. Add the type string to `probable_intel/nexus/parser.py` → `_VALID_NODE_TYPES` set
5. Write a unit test under `tests/unit/nodes/`

## NEXUS DSL (.nx files)

Files are YAML with NEXUS schema enforcement. Top-level keys:

```yaml
apparatus_name: "my-mission"
version: 1.0
trust_level: unclassified | restricted | classified | top_secret
owner: "handle-only"        # never a real name or email

nodes:
  - type: FeedNode           # PascalCase node type
    id: "feed.my-source"     # dot-notation id, unique per apparatus
    targets:
      - feed: "https://..."
    schedule:
      interval: "15m"        # s / m / h / d suffix
      jitter: 60             # seconds of random delay
    filters:
      keywords: ["kw1"]
      min_word_count: 50
    emit:
      channel: "raw.feed.my"
      priority: high
    subscribe:
      channels: ["some.channel"]
    rules:
      - condition: "sentiment_score < -0.5"
        severity: HIGH
        label: "label-string"
    backend:
      primary: "vader"
      fallback: "llm"
    llm:
      provider: "ollama"           # anthropic | ollama | vllm
      model: "llama3.2"
      base_url: "http://localhost:11434"
      budget_per_day_usd: 0.0     # 0 = no budget cap (local providers)
    honeypots:
      - type: "fake_api_endpoint"
        path: "/api/v1/fake"
        canary_id: "canary-01"
    config:                  # node-specific tuning; flattened into the node's config
      persist_path: "/data/kg.json"   # (e.g. KnowledgeGraphNode)
      # StorageNode: vector_index: false to skip semantic indexing (on when [semantic]
      # is installed); embedding_model: "all-MiniLM-L6-v2" (must match `pi search --model`)
      # ThreatAssessNode: attack_mapping: false to skip ATT&CK tagging, or a path to
      # override nexus/data/attack_mapping.yaml

spine:                     # optional; default is the in-process bus
  backend: redis             # memory | redis — chosen by the first apparatus a hub loads
  url: "redis://localhost:6379/0"
  max_len: 10000             # approximate per-channel stream cap

federation:
  enabled: true
  peers:
    - url: "http://hub-b.internal:8080"
      api_key_env: "PEER_B_KEY"
  auto_federate_critical: true
  ingest_channels: ["threat.*"]

storage:
  primary:
    backend: sqlite
    path: /data/probable-intel/main.db
```

**Validator rules** (enforced at load time):
- Circular channel routes → `NEXUSError`
- LLM backend on non-analyst nodes → `NEXUSError`
- Emitted channel with no subscriber (non-sink) → `NEXUSWarning`

## Rule condition syntax

Used in `ThreatAssessNode` rules. Operates on `IntelPacket.payload` fields:

```
sentiment_score < -0.5
entity.type == "CVE" AND sentiment_score < -0.3
tags contains "ransomware"
NOT sentiment_score > 0
```

Operators: `==  !=  >  <  >=  <=  AND  OR  NOT  contains`
Field access: dot-notation (`entity.type`, `threat.score`)

## Severity ranking

`_SEVERITY_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}` — always use this dict for severity comparisons, never string `max()`.

## Key invariants

- Nodes **never** communicate directly — only via `Spine.publish()` / `Spine.subscribe()`.
  The only exceptions are service references the Hub wires at load time: AttributionNode → KnowledgeGraphNode (`_kg_node`, read-only queries) and every node → the apparatus's FingerprintDefenseNode (`_request_guard`, used by harvesters via `BaseNode._before_request()` before each outbound request). Packet data still flows only over the Spine.
- `source_hash` is set once at packet creation and preserved through all `relay()` hops — use it for dedup, not `packet_id`
- Operator identity never appears in `.nx` files, logs, or code — handles only
- `SecretManager` (`hub/secrets.py`) is the only place that reads `.env`; never access `os.environ` directly for secrets elsewhere
- `StorageNode` uses `INSERT OR IGNORE` keyed on `packet_id` — re-delivering the same packet is a no-op. Every pipeline stage of one article (shared `source_hash`) is stored as its own row; the semantic index embeds each `source_hash` once

## CI

GitHub Actions (`.github/workflows/ci.yml`) runs on every push and PR:
- Matrix: Python 3.11, 3.12
- Steps: install deps → `pi validate` sample apparatus → `pytest tests/unit/` → `pytest tests/integration/`
- spaCy model is **not** downloaded in CI; `EntityExtractorNode` degrades gracefully

## Environment

Copy `.env.example` to `.env`. Required for full operation:

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic Claude provider (SentimentNode, NarrativeNode, etc.) |
| `SQLITE_PATH` | Override default DB path (a node `path` or apparatus `storage.primary.path` takes precedence) |
| `ALERT_WEBHOOK_URL` | AlertNode webhook sink |
| `MASTER_KEY` | Encryption key for classified packets |
| `HUB_BIND_HOST` | HubAPI bind address (default `127.0.0.1`) |

Local providers (Ollama, vLLM) require no API key — set `base_url` in the `llm:` block instead.

## Implemented node catalog

| Tier | Node | Description |
|---|---|---|
| Harvester | `FeedNode` | RSS/Atom feed polling |
| Harvester | `WebNode` | Web page scraping (Playwright-capable) |
| Harvester | `ApiNode` | Authenticated JSON REST APIs (NVD, CIRCL, etc.) |
| Harvester | `SocialNode` | Reddit, HackerNews, Mastodon |
| Harvester | `ReconNode` | Autonomous OSINT expansion via KG entity leads |
| Analyst | `SentimentNode` | VADER + LLM fallback |
| Analyst | `EntityExtractorNode` | spaCy NER + LLM fallback |
| Analyst | `ThreatAssessNode` | Rule-based threat scoring + MITRE ATT&CK tagging (`attack_techniques`) |
| Analyst | `NarrativeNode` | LLM-powered rolling-window synthesis |
| Sentinel | `AnomalyNode` | Rolling-baseline z-score anomaly detection |
| Sentinel | `AlertNode` | Webhook + logfile alerting |
| Archivist | `StorageNode` | SQLite persistence + semantic index for `pi search` (with `[semantic]`) |
| Archivist | `KnowledgeGraphNode` | Entity co-occurrence graph (NetworkX) |
| Coordinator | `TaskRouterNode` | OODA loop — LLM-enhanced collection directives |
| CounterIntel | `OpSecNode` | Request-pattern auditing |
| CounterIntel | `DeceptionNode` | Honeypot endpoints + canary tokens |
| CounterIntel | `FingerprintDefenseNode` | Per-request jitter + request audit (`ci.opsec.audit`) for every harvester in its apparatus (Hub-wired) |
| CounterIntel | `AttributionNode` | Passive adversary profiling from honeypot hits |

## What's deferred (genuinely not built)

- Neo4j backend for `KnowledgeGraphNode` (currently NetworkX in-memory only)
- Textual TUI dashboard
- `MonitorNode` (continuous watch over long-lived targets, distinct from AnomalyNode)
- Predictive threat modeling (time-series over KG history)
- Autonomous OSINT expansion via dark web sources (DarkWebNode)
