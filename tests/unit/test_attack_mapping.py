"""Unit tests for the static MITRE ATT&CK lookup."""
from __future__ import annotations

import pytest

from probable_intel.nodes.analysts.attack_mapping import AttackMapping, Technique

T = Technique


def test_shipped_mapping_loads_and_is_consistent():
    mapping = AttackMapping.load()  # validates ids and that every reference exists
    assert {t["id"] for t in mapping.match([], "ransomware")} == {"T1486"}


def test_keywords_match_whole_words_and_phrases_case_insensitively():
    mapping = AttackMapping.load()
    ids = lambda text: [t["id"] for t in mapping.match([], text)]  # noqa: E731

    assert ids("New PHISHING wave") == ["T1566"]
    assert ids("antiphishingtool update") == []  # not inside another word
    assert ids("attackers used password spraying") == ["T1110.003"]
    assert ids("a drive-by download") == ["T1189"]
    assert ids("routine patch notes") == []


def test_labels_and_keywords_combine_without_duplicates():
    mapping = AttackMapping.load()
    techniques = mapping.match(["ransomware-activity", "unmapped-label"], "ransomware and phishing")

    assert [t["id"] for t in techniques] == ["T1486", "T1566"]  # labels first, then keywords
    assert techniques[0] == {"id": "T1486", "name": "Data Encrypted for Impact", "tactics": ["impact"]}


def test_reference_to_unknown_technique_is_rejected():
    with pytest.raises(ValueError, match="unknown technique"):
        AttackMapping({"T1486": T("T1486", "x", ("impact",))}, {"wiper": ["T1485"]}, {})


def test_malformed_technique_id_is_rejected():
    with pytest.raises(ValueError, match="invalid ATT&CK technique id"):
        AttackMapping({"1486": T("1486", "x", ("impact",))}, {}, {})
