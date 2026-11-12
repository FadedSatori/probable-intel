from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from ..base import BaseNode
from ...nexus.spec import RuleSpec
from ...spine.packet import IntelPacket, Priority, TrustLevel

if TYPE_CHECKING:
    from ...nexus.spec import NodeSpec
    from ...spine.spine import Spine

log = logging.getLogger(__name__)

_SEVERITY_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}

_SEVERITY_PRIORITY = {
    "LOW": Priority.NORMAL,
    "MEDIUM": Priority.HIGH,
    "HIGH": Priority.HIGH,
    "CRITICAL": Priority.CRITICAL,
}


# A quoted string, or a standalone AND / OR. Quoted strings come first in the pattern so
# that a keyword or operator inside a value is never read as syntax. A quote glued to the
# end of a word (O'Brien) is an apostrophe, not the start of a string.
_LOGICAL = re.compile(
    r'''(?<!\w)"[^"]*"|(?<!\w)'[^']*'|(?<!\S)(?P<keyword>AND|OR)(?!\S)'''
)
_CONTAINS = re.compile(r"^(\S+)\s+contains\s+(.+)$", re.DOTALL)
_COMPARISON = re.compile(r"^(\S+?)\s*(==|!=|>=|<=|>|<)\s*(.+)$", re.DOTALL)


def _split(condition: str, keyword: str) -> list[str]:
    """Split on a logical keyword, leaving quoted text alone."""
    parts, start = [], 0
    for m in _LOGICAL.finditer(condition):
        if m.group("keyword") == keyword:
            parts.append(condition[start:m.start()])
            start = m.end()
    parts.append(condition[start:])
    return parts


def _eval_condition(condition: str, payload: dict) -> bool:
    """Evaluate a DSL rule condition against a packet payload.

    Comparisons are `field.path OP value` (== != > < >= <=) and `field contains value`,
    with fields reached by dot notation. They combine with NOT, AND and OR: NOT binds
    tightest and OR loosest, as in SQL and Python. Quote a value that contains spaces,
    keywords or operators.
    """
    condition = condition.strip()

    # Split on the loosest operator first, so the tighter ones group inside each part.
    for keyword, combine in (("OR", any), ("AND", all)):
        parts = _split(condition, keyword)
        if len(parts) > 1:
            return combine(_eval_condition(p, payload) for p in parts)

    # Handle NOT
    if condition.startswith("NOT "):
        return not _eval_condition(condition[4:], payload)

    # Handle: field contains "value"
    m = _CONTAINS.match(condition)
    if m:
        field_path, val_expr = m.group(1), m.group(2).strip().strip('"').strip("'")
        field_val = _get_field(field_path, payload)
        if field_val is None:
            return False
        if isinstance(field_val, list):
            return val_expr in field_val
        return val_expr in str(field_val)

    # Handle comparison: field.path OP value
    m = _COMPARISON.match(condition)
    if m:
        left, op, right = m.group(1), m.group(2), m.group(3).strip().strip('"').strip("'")
        field_val = _get_field(left, payload)
        if field_val is None:
            return False
        try:
            if op == "==":
                return str(field_val) == right
            if op == "!=":
                return str(field_val) != right
            fv = float(field_val)
            rv = float(right)
            if op == ">=":
                return fv >= rv
            if op == "<=":
                return fv <= rv
            if op == ">":
                return fv > rv
            if op == "<":
                return fv < rv
        except (ValueError, TypeError):
            return False

    return False


def _get_field(path: str, payload: dict) -> object:
    """Traverse dot-notation path in payload dict."""
    parts = path.split(".")
    val: object = payload
    for part in parts:
        if isinstance(val, dict):
            val = val.get(part)
        else:
            return None
    return val


class ThreatAssessNode(BaseNode):
    """Evaluates DSL-defined rules against incoming packets; emits ThreatPackets."""

    def __init__(self, spec: "NodeSpec", spine: "Spine") -> None:
        super().__init__(spec, spine)
        self._rules: list[RuleSpec] = []
        self._emit_channel: str = ""
        self._emit_priority: Priority = Priority.HIGH

    async def setup(self) -> None:
        self._rules = self.spec.rules
        if self.spec.emit:
            self._emit_channel = self.spec.emit.channel
            self._emit_priority = Priority[self.spec.emit.priority.upper()]
        self.subscribe()

    async def teardown(self) -> None:
        self.unsubscribe()

    async def run(self) -> None:
        packet = await self.receive()
        await self._assess(packet)

    async def _assess(self, packet: IntelPacket) -> None:
        matched = []
        for rule in self._rules:
            try:
                if _eval_condition(rule.condition, packet.payload):
                    matched.append({"severity": rule.severity, "label": rule.label})
            except Exception as e:
                log.warning("rule eval error for %r: %s", rule.condition, e)

        if not matched:
            return

        max_severity = max(matched, key=lambda m: _SEVERITY_RANK.get(m["severity"], 0))["severity"]
        out_priority = _SEVERITY_PRIORITY.get(max_severity, Priority.NORMAL)

        out = packet.relay(
            self.node_id,
            self._emit_channel,
            packet_type="ThreatPacket",
            payload={
                **packet.payload,
                "threat_matches": matched,
                "max_severity": max_severity,
                "rule_count": len(matched),
            },
            priority=out_priority,
        )
        await self.emit(self._emit_channel, out)
        log.info(
            "node %s: THREAT [%s] matched %d rule(s) in packet %s",
            self.node_id,
            max_severity,
            len(matched),
            packet.packet_id,
        )
