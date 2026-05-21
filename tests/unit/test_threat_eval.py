import pytest
from probable_intel.nodes.analysts.threat_node import _eval_condition, _get_field


def test_simple_equality():
    assert _eval_condition('severity == "HIGH"', {"severity": "HIGH"}) is True
    assert _eval_condition('severity == "LOW"', {"severity": "HIGH"}) is False


def test_numeric_comparison():
    assert _eval_condition("sentiment_score < -0.5", {"sentiment_score": -0.7}) is True
    assert _eval_condition("sentiment_score < -0.5", {"sentiment_score": -0.3}) is False
    assert _eval_condition("confidence >= 0.9", {"confidence": 0.95}) is True


def test_and_operator():
    payload = {"sentiment_score": -0.8, "entity_type": "cve"}
    assert _eval_condition(
        'sentiment_score < -0.5 AND entity_type == "cve"', payload
    ) is True
    assert _eval_condition(
        'sentiment_score > 0 AND entity_type == "cve"', payload
    ) is False


def test_or_operator():
    payload = {"severity": "LOW"}
    assert _eval_condition('severity == "LOW" OR severity == "HIGH"', payload) is True
    assert _eval_condition('severity == "HIGH" OR severity == "CRITICAL"', payload) is False


def test_not_operator():
    payload = {"severity": "LOW"}
    assert _eval_condition('NOT severity == "HIGH"', payload) is True
    assert _eval_condition('NOT severity == "LOW"', payload) is False


def test_contains_list():
    payload = {"tags": ["breach", "ransomware"]}
    assert _eval_condition("tags contains breach", payload) is True
    assert _eval_condition("tags contains exploit", payload) is False


def test_missing_field_returns_false():
    assert _eval_condition("nonexistent_field < 0", {}) is False


def test_get_field_dot_notation():
    payload = {"entity": {"type": "person", "name": "Alice"}}
    assert _get_field("entity.type", payload) == "person"
    assert _get_field("entity.name", payload) == "Alice"
    assert _get_field("entity.missing", payload) is None
