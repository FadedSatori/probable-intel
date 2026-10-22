"""Belief arithmetic, including the poisoning property from DESIGN.md §6.1 / §12 C14."""
from __future__ import annotations

import random

import pytest

from probable_intel.anamnesis.beliefs import (
    TIER_CAP,
    band,
    capped_update,
    chain_cap,
    claim_cap,
    surprise_bits,
)


def test_chain_is_as_strong_as_weakest_link():
    assert chain_cap(["T1", "T3"]) == TIER_CAP["T3"]
    with pytest.raises(ValueError):
        chain_cap([])


def test_weak_evidence_never_lowers_a_claims_cap():
    # Regression for the defect found in failure-mode analysis: min over all evidence.
    assert claim_cap([["T1"], ["TQ"]]) == TIER_CAP["T1"]
    assert claim_cap([]) == TIER_CAP["TQ"]


def test_capped_update_raises_only_to_cap_and_lowers_freely():
    assert capped_update(0.5, 0.99, [["T1", "T3"]]) == pytest.approx(0.65)
    assert capped_update(0.5, 0.99, [["T1", "T3"], ["T1"]]) == pytest.approx(0.95)
    assert capped_update(0.9, 0.95, [["T3"]]) == 0.9  # already above cap: hold, don't jump
    assert capped_update(0.9, 0.2, [["TQ"]]) == 0.2  # lowering is arithmetic; review gating is policy


def test_poisoning_property_low_tier_floods_cannot_raise_belief_above_cap():
    rng = random.Random(1234)
    for _ in range(500):
        start = rng.random() * TIER_CAP["T3"]
        p = start
        for _ in range(rng.randint(1, 40)):
            chains = [[rng.choice(["T3", "TQ"]) for _ in range(rng.randint(1, 3))]
                      for _ in range(rng.randint(1, 4))]
            p = capped_update(p, rng.random(), chains)
        assert p <= TIER_CAP["T3"] + 1e-12


@pytest.mark.parametrize(
    "p,label",
    [
        (0.0, "almost no chance"), (0.05, "almost no chance"), (0.051, "very unlikely"),
        (0.2, "very unlikely"), (0.3, "unlikely"), (0.5, "roughly even chance"),
        (0.8, "likely"), (0.81, "very likely"), (0.96, "almost certain"), (1.0, "almost certain"),
    ],
)
def test_icd203_bands(p, label):
    assert band(p) == label


def test_band_rejects_out_of_range():
    with pytest.raises(ValueError):
        band(1.2)


def test_surprise_is_zero_for_no_change_and_grows_with_movement():
    assert surprise_bits(0.5, 0.5) == pytest.approx(0.0, abs=1e-12)
    assert 0 < surprise_bits(0.5, 0.6) < surprise_bits(0.5, 0.9) < surprise_bits(0.1, 0.9)
    assert surprise_bits(0.0, 1.0) > 10  # clamped, finite
