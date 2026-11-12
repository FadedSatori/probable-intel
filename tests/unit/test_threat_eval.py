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


@pytest.mark.parametrize("condition, expected", [
    ("a == 1 OR b == 1 AND c == 1", True),   # a OR (b AND c): true through a alone
    ("c == 1 AND b == 1 OR a == 1", True),   # (c AND b) OR a: true through a alone
    ("a == 0 OR b == 1 AND c == 1", False),  # nothing on either side holds
    ("NOT c == 1 AND a == 0 OR b == 1", True),  # ((NOT c) AND a==0) OR b
])
def test_and_binds_tighter_than_or(condition: str, expected: bool):
    assert _eval_condition(condition, {"a": 1, "b": 1, "c": 0}) is expected


def test_keywords_inside_quotes_are_part_of_the_value():
    payload = {"title": "Command AND Control beacon", "note": "this OR that"}
    assert _eval_condition('title contains "Command AND Control"', payload) is True
    assert _eval_condition('note == "this OR that"', payload) is True


def test_an_apostrophe_inside_a_bare_word_does_not_open_a_quote():
    payload = {"name": "Shaun O'Brien", "org": "D'Arcy Ltd"}
    assert _eval_condition("name contains O'Brien AND org contains D'Arcy", payload) is True
    assert _eval_condition("name contains O'Brien AND org contains D'Souza", payload) is False


def test_operator_characters_inside_a_value_are_part_of_the_value():
    payload = {"content": "<script>alert(1)</script>", "expr": "a==b"}
    assert _eval_condition('content contains "<script>"', payload) is True
    assert _eval_condition('expr != "x==y"', payload) is True
    assert _eval_condition('expr == "a==b"', payload) is True


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
