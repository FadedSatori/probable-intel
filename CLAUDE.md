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

# Run all tests
python -m pytest

# Run by tier
python -m pytest tests/unit/
python -m pytest tests/integration/

# Lint / type check
ruff check .
mypy probable_intel/

# CLI
pi validate nexus/apparatuses/mvp-demo.nx
pi run      nexus/apparatuses/mvp-demo.nx
pi status   nexus/apparatuses/mvp-demo.nx
pi watch    nexus/apparatuses/mvp-demo.nx [--channel raw.feed.security]
```

## Project layout

```
probable_intel/
├── spine/          # IntelPacket, Spine (async bus), Channel (priority lanes)
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
├── storage/        # SQLiteBackend (async, dedup via INSERT OR IGNORE)
└── cli/            # Typer app — validate / run / status / watch

nexus/
├── apparatuses/    # Operator .nx files (one per mission)
├── policies/       # Reusable OPSEC / rate-limit policy fragments
└── templates/      # Alert templates (Jinja2)

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
                    Spine (named channels, 4 priority lanes each)
                              ↓
                    Nodes communicate only via IntelPacket on channels
```

**Spine** — `spine.publish(channel, packet)` fans out to all subscribers. Each channel has four `asyncio.Queue` lanes (CRITICAL / HIGH / NORMAL / LOW). `get()` drains highest priority first.

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

- Nodes **never** communicate directly — only via `Spine.publish()` / `Spine.subscribe()`
- `source_hash` is set once at packet creation and preserved through all `relay()` hops — use it for dedup, not `packet_id`
- Operator identity never appears in `.nx` files, logs, or code — handles only
- `SecretManager` (`hub/secrets.py`) is the only place that reads `.env`; never access `os.environ` directly for secrets elsewhere
- `StorageNode` uses `INSERT OR IGNORE` — duplicate `source_hash` is silently dropped, not an error

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
| `SQLITE_PATH` | Override default DB path |
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
| Analyst | `ThreatAssessNode` | Rule-based threat scoring |
| Analyst | `NarrativeNode` | LLM-powered rolling-window synthesis |
| Sentinel | `AnomalyNode` | Rolling-baseline z-score anomaly detection |
| Sentinel | `AlertNode` | Webhook + logfile alerting |
| Archivist | `StorageNode` | SQLite persistence |
| Archivist | `KnowledgeGraphNode` | Entity co-occurrence graph (NetworkX) |
| Coordinator | `TaskRouterNode` | OODA loop — LLM-enhanced collection directives |
| CounterIntel | `OpSecNode` | Request-pattern auditing |
| CounterIntel | `DeceptionNode` | Honeypot endpoints + canary tokens |
| CounterIntel | `FingerprintDefenseNode` | Browser identity injection |
| CounterIntel | `AttributionNode` | Passive adversary profiling from honeypot hits |

## What's deferred (genuinely not built)

- Redis Spine adapter (replace asyncio.Queue with Redis streams for multi-process scale-out)
- Neo4j backend for `KnowledgeGraphNode` (currently NetworkX in-memory only)
- Textual TUI dashboard
- `MonitorNode` (continuous watch over long-lived targets, distinct from AnomalyNode)
- Semantic memory layer (vector embeddings + similarity retrieval)
- Predictive threat modeling (time-series over KG history)
- MITRE ATT&CK mapping + STIX 2.1 export
- Autonomous OSINT expansion via dark web sources (DarkWebNode)
