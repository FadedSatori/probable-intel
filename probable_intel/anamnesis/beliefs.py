"""Belief arithmetic: provenance caps, ICD-203 bands, Bayesian surprise.

See DESIGN.md §6. Pure functions only — the ledger itself is an append-only
JSONL file and every number here is recomputable from it.
"""
from __future__ import annotations

from math import log

# Highest probability a claim may be RAISED to on the strength of one evidence chain.
TIER_CAP: dict[str, float] = {"T0": 1.0, "T1": 0.95, "T2": 0.85, "T3": 0.65, "TQ": 0.5}

# ICD 203 expressions of likelihood: (inclusive upper bound, label).
ICD203: tuple[tuple[float, str], ...] = (
    (0.05, "almost no chance"),
    (0.20, "very unlikely"),
    (0.45, "unlikely"),
    (0.55, "roughly even chance"),
    (0.80, "likely"),
    (0.95, "very likely"),
    (1.00, "almost certain"),
)


def chain_cap(chain: list[str]) -> float:
    """An evidence chain is only as strong as its weakest link."""
    if not chain:
        raise ValueError("empty evidence chain")
    return min(TIER_CAP[t] for t in chain)


def claim_cap(chains: list[list[str]]) -> float:
    """A claim is capped by its best independent chain; weak evidence never lowers the cap."""
    return max((chain_cap(c) for c in chains), default=TIER_CAP["TQ"])


def capped_update(p_prior: float, p_proposed: float, chains: list[list[str]]) -> float:
    """Evidence may lower belief freely, but may only raise it up to the provenance cap.

    Downgrades driven only by T3/TQ evidence should be routed to review by the
    caller rather than applied (DESIGN.md §6.1); this function is the arithmetic.
    """
    _check_p(p_prior)
    _check_p(p_proposed)
    if p_proposed <= p_prior:
        return p_proposed
    return min(p_proposed, max(p_prior, claim_cap(chains)))


def band(p: float) -> str:
    _check_p(p)
    return next(label for upper, label in ICD203 if p <= upper)


def surprise_bits(p: float, q: float, eps: float = 1e-6) -> float:
    """KL(Bern(q) || Bern(p)) in bits: how far evidence moved a belief from p to q."""
    _check_p(p)
    _check_p(q)
    p, q = min(max(p, eps), 1 - eps), min(max(q, eps), 1 - eps)
    return (q * log(q / p) + (1 - q) * log((1 - q) / (1 - p))) / log(2)


def _check_p(p: float) -> None:
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"probability out of range: {p}")
