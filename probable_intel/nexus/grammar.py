"""NEXUS DSL grammar reference — documents the language schema.

NEXUS files (.nx) use YAML syntax with domain-specific schema enforcement.
The parser (parser.py) handles both the pure YAML form and a syntactic-sugar
form with apparatus/node declarations that are pre-processed into YAML.

NEXUS Syntax (sugar form):
    apparatus "name":
      version: 1.0
      trust_level: classified | restricted | unclassified | top_secret
      owner: "handle"

      node NodeType "node-id":
        targets:
          - feed: "https://..."
          - url: "https://..."
        schedule:
          interval: 15m | 2h | 1d
          cron: "0 * * * *"
          jitter: 60
        filters:
          keywords: ["kw1", "kw2"]
          exclude_keywords: ["kw3"]
          min_word_count: 50
        subscribe:
          channels: ["channel.name"]
        emit:
          channel: "channel.name"
          priority: low | normal | high | critical
        rules:
          - condition: "field.path OP value AND/OR ..."
            severity: LOW | MEDIUM | HIGH | CRITICAL
            label: "rule-label"
        backend:
          primary: "vader"
          fallback: "llm"
          llm_threshold: 0.4
        identity:
          profile: rotate | fixed | stealth
          proxy_pool: "pool-name"
        rate_limit:
          requests_per_minute: 30
        honeypots:
          - type: "fake_api_endpoint"
            path: "/api/v1/internal/nodes"
            canary_id: "canary-01"

      router:
        overflow_policy: drop_low_priority | queue
        backpressure_threshold: 1000

      llm:
        provider: anthropic
        model: claude-opus-4-5
        api_key_env: ANTHROPIC_API_KEY
        budget_per_day_usd: 5.0

      storage:
        primary:
          backend: sqlite | postgres
          path: /data/probable-intel/main.db
        archive:
          backend: s3
          bucket: my-bucket
          after_days: 30

Valid Node Types:
  Harvesters:   WebNode, FeedNode, SocialNode, ApiNode
  Analysts:     SentimentNode, EntityExtractorNode, ThreatAssessNode, NarrativeNode
  Sentinels:    MonitorNode, AnomalyNode, AlertNode
  Archivists:   StorageNode, KnowledgeGraphNode
  Coordinators: TaskRouterNode
  CounterIntel: OpSecNode, DeceptionNode, FingerprintDefenseNode, AttributionNode

Condition Expression Operators (in rules):
  ==, !=, >, <, >=, <=
  AND, OR, NOT
  field contains "value"
  dot-notation for nested fields: entity.type, sentiment.score
"""

NEXUS_VERSION = "1.0"
