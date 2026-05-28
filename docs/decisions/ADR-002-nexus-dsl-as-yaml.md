# ADR-002: NEXUS DSL as validated YAML, not a custom grammar

**Status:** Accepted

## Context

The system needed a way for operators to define collection missions without writing Python. Options considered: custom Lark grammar, YAML with schema validation, TOML, JSON.

## Decision

NEXUS files are YAML with a custom schema enforced by a Python validator (`probable_intel/nexus/validator.py`). The parser (`parser.py`) uses PyYAML + dataclasses, not Lark.

Note: `pyproject.toml` lists `lark` as a dependency — this is vestigial from an early prototype. Lark is not used in the current implementation.

## Rationale

- **Operator familiarity** — YAML is universally known; a custom grammar would require documentation and tooling
- **Editor support** — YAML has syntax highlighting and linting in all editors; custom grammars do not
- **Sufficient expressiveness** — the DSL only needs to express: node type, config, targets, filters, emit channel, subscribe channels, rules, schedule. YAML handles all of this cleanly.
- **Validation is the differentiator** — what makes NEXUS valuable is topology validation (cycle detection, orphan channels, trust-level checks), not parsing syntax

## Consequences

- No Turing-complete features (loops, conditionals) in apparatus files — intentional, keeps configs declarative and auditable
- Adding new config keys requires updating `probable_intel/nexus/spec.py` (dataclasses) and `parser.py` (extraction logic)
- `lark` in pyproject.toml should be removed in a future cleanup pass (it's unused)
