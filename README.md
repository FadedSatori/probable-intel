# probable-intel

Autonomous intelligence node network. Operators define collection missions in `.nx` files using the **NEXUS DSL** — the system does the rest: harvest, analyze, correlate, store, and alert, with a built-in counterintelligence layer that keeps the infrastructure invisible.

```
Feed sources → FeedNode → SentimentNode → ThreatAssessNode → AlertNode
                                        ↘ EntityExtractorNode → StorageNode
                                        CI: OpSecNode · DeceptionNode · FingerprintDefenseNode
```

## Requirements

- Python 3.11+

## Install

```bash
git clone <repo>
cd probable-intel

pip install -e ".[dev]"
pip install atoma vaderSentiment aiosqlite httpx pyyaml

# Optional: full NER (requires ~40 MB model download)
pip install -e ".[nlp]"
python -m spacy download en_core_web_sm

# Optional: browser-based collection + fingerprint defense
pip install -e ".[browser]"
playwright install chromium
```

Copy the environment template and fill in values:

```bash
cp .env.example .env
```

## Quickstart

```bash
# Validate an apparatus file (no network, no side effects)
pi validate nexus/apparatuses/mvp-demo.nx

# Run the full apparatus
pi run nexus/apparatuses/mvp-demo.nx

# Watch intelligence packets flow in real-time
pi watch nexus/apparatuses/mvp-demo.nx

# Watch a specific channel
pi watch nexus/apparatuses/mvp-demo.nx --channel threat.security

# Dump apparatus metadata as JSON
pi status nexus/apparatuses/mvp-demo.nx
```

## NEXUS DSL

Operators write `.nx` files to define what the system collects, how it analyzes it, and where results go. No Python required.

### Minimal example

```yaml
apparatus_name: "my-mission"
version: 1.0
trust_level: restricted
owner: "ops-handle"

nodes:
  - type: FeedNode
    id: "feed.krebs"
    targets:
      - feed: "https://krebsonsecurity.com/feed/"
    schedule:
      interval: "30m"
      jitter: 120
    filters:
      keywords: ["breach", "vulnerability", "zero-day"]
      min_word_count: 50
    emit:
      channel: "raw.feed.security"
      priority: high

  - type: SentimentNode
    id: "analyst.sentiment"
    subscribe:
      channels: ["raw.feed.security"]
    backend:
      primary: "vader"
    emit:
      channel: "analysis.sentiment"
      priority: normal

  - type: ThreatAssessNode
    id: "analyst.threat"
    subscribe:
      channels: ["analysis.sentiment"]
    rules:
      - condition: "sentiment_score < -0.5"
        severity: HIGH
        label: "negative-security-signal"
      - condition: "sentiment_score < -0.7"
        severity: CRITICAL
        label: "critical-threat-indicator"
    emit:
      channel: "threat.security"
      priority: high

  - type: StorageNode
    id: "archivist.main"
    subscribe:
      channels: ["threat.security", "analysis.sentiment"]
    emit:
      channel: "sink.storage"
      priority: low

storage:
  primary:
    backend: sqlite
    path: /data/probable-intel/main.db
```

### Schedule intervals

`interval` accepts a number with a suffix: `30s`, `15m`, `2h`, `1d`. Add `jitter` (seconds) for randomized timing that avoids detectable periodicity.

### Rule conditions

Rules in `ThreatAssessNode` evaluate against `IntelPacket.payload` fields:

```yaml
rules:
  - condition: "sentiment_score < -0.5"
    severity: HIGH
    label: "negative-signal"

  - condition: "sentiment_score < -0.7 AND entity.type == CVE"
    severity: CRITICAL
    label: "active-exploit"

  - condition: "tags contains ransomware"
    severity: HIGH
    label: "ransomware-indicator"
```

Operators: `==  !=  >  <  >=  <=  AND  OR  NOT  contains`

Severity ranking: `LOW < MEDIUM < HIGH < CRITICAL`

### Trust levels

```yaml
trust_level: unclassified | restricted | classified | top_secret
```

Nodes can only receive packets at or below their apparatus trust level. Violations are caught at load time.

## Node catalog

### Harvesters

| Node | Description |
|---|---|
| `FeedNode` | RSS/Atom ingestion with keyword filtering and dedup |
| `WebNode` | HTTP scraping with realistic browser headers |

### Analysts

| Node | Description |
|---|---|
| `SentimentNode` | VADER rule-based sentiment scoring; LLM fallback planned |
| `EntityExtractorNode` | spaCy NER — degrades gracefully if model not installed |
| `ThreatAssessNode` | DSL rule evaluator; emits `ThreatPacket` with severity |

### Sentinels

| Node | Description |
|---|---|
| `AlertNode` | Fires on incoming packets; sinks to logfile and/or webhook |

### Archivists

| Node | Description |
|---|---|
| `StorageNode` | Persists `IntelPacket`s to SQLite with dedup |

### CounterIntel

| Node | Description |
|---|---|
| `OpSecNode` | Monitors identity reuse, proxy pool health, request periodicity |
| `DeceptionNode` | Honeypot endpoints + canary tokens; fires `DeceptionTriggerPacket` on hit |
| `FingerprintDefenseNode` | Injects log-normal timing jitter into harvester requests |

## Architecture

```
Operator writes .nx files
         ↓
   NexusParser → ApparatusSpec
         ↓
   ApparatusValidator
   (cycle detection, orphan channels, trust-level checks)
         ↓
   Hub (orchestrator)
   ├── NodeRegistry          node id → instance + state
   ├── NodeFactory           NodeSpec → Python class
   ├── NodeLifecycleManager  DECLARED → RUNNING → ERROR/STOPPED
   ├── HealthMonitor         heartbeat every 15s
   └── HubAPI                FastAPI — status + honeypot routes
         ↓
   Spine (async message bus)
   └── Channels with 4 priority lanes (CRITICAL / HIGH / NORMAL / LOW)
         ↓
   Nodes ←→ IntelPackets ←→ Nodes
```

**IntelPacket** fields of note:

| Field | Purpose |
|---|---|
| `packet_type` | String tag: `RawFeedPacket`, `SentimentPacket`, `ThreatPacket`, … |
| `priority` | `LOW=1  NORMAL=2  HIGH=3  CRITICAL=4` |
| `trust_level` | `UNCLASSIFIED=1  RESTRICTED=2  CLASSIFIED=3  TOP_SECRET=4` |
| `provenance` | Ordered list of node IDs that have touched this packet |
| `source_hash` | SHA-256 of original content — stable across relay hops, used for dedup |
| `payload` | Arbitrary dict; schema is per packet_type convention |

## pi watch output

```
[12:34:01] [HIGH    ] RawFeedPacket            #a3f2c1d0  feed.krebs-security
[12:34:02] [NORMAL  ] SentimentPacket          #a3f2c1d0  analyst.sentiment → feed.krebs-security
[12:34:02] [HIGH    ] ThreatPacket             #a3f2c1d0  analyst.threat → analyst.sentiment
```

Pass `--raw` for newline-delimited JSON instead of color output (useful for piping to `jq`).

## HubAPI honeypots

When `api_port` is set, the Hub exposes a FastAPI server. In addition to real status endpoints, it serves synthetic honeypot routes that fire a `DeceptionTriggerPacket` on access:

- `GET /api/v1/nodes/list` — fake node listing
- `GET /api/v1/tasks/pending` — fake task queue
- `GET /admin/status` — fake admin dashboard
- `GET /beacon/{token}` — canary beacon (returns a transparent 1×1 GIF)

Any hit appears immediately on the `ci.deception.triggers` channel.

## Development

```bash
pytest                          # all tests
pytest tests/unit/ -v           # unit only
pytest tests/integration/ -v    # integration only
ruff check .                    # lint
mypy probable_intel/            # type check
```

See [CLAUDE.md](CLAUDE.md) for architecture details, adding new node types, and what's deferred post-MVP.

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | When LLM wired | Claude API access |
| `SQLITE_PATH` | No | Override default DB location |
| `ALERT_WEBHOOK_URL` | No | AlertNode webhook sink |
| `MASTER_KEY` | For classified | Encryption key for at-rest intel |
| `HUB_BIND_HOST` | No | HubAPI bind address (default `127.0.0.1`) |
| `PROXY_POOL_ENDPOINT` | No | Residential proxy pool for harvesters |

See `.env.example` for the full list.

## Roadmap

- **LLM integration** — Claude API fallback in `SentimentNode` and `EntityExtractorNode`; daily budget cap via `LLMRouter`
- `SocialNode` — social media collection
- `NarrativeNode` — memetic/narrative tracking
- `KnowledgeGraphNode` — persistent entity graph (NetworkX → Neo4j)
- `AnomalyNode` — statistical anomaly detection over time series
- `AttributionNode` — passive adversary profiling from `DeceptionTriggerPacket`s
- Redis Spine adapter — multi-process / multi-host deployment
- Textual TUI dashboard — live node status and packet stream in terminal
