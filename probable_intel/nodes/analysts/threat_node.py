from __future__ import annotations

import asyncio
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


def _eval_condition(condition: str, payload: dict) -> bool:
    """Simple condition evaluator for DSL rule conditions.

    Supports: field.path == value, AND, OR, NOT, contains().
    Operates on packet payload fields accessed by dot notation.
    """
    condition = condition.strip()

    # Handle AND/OR at the top level (left-to-right, no precedence)
    for op, fn in [(" AND ", all), (" OR ", any)]:
        if op in condition:
            parts = condition.split(op, 1)
            results = [_eval_condition(p, payload) for p in parts]
            return fn(results)

    # Handle NOT
    if condition.startswith("NOT "):
        return not _eval_condition(condition[4:], payload)

    # Handle comparison: field.path OP value
    for op in ("==", "!=", ">=", "<=", ">", "<"):
        if op in condition:
            left, _, right = condition.partition(op)
            left = left.strip()
            right = right.strip().strip('"').strip("'")
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

    # Handle: field contains "value"
    m = re.match(r"^(\S+)\s+contains\s+(.+)$", condition)
    if m:
        field_path, val_expr = m.group(1), m.group(2).strip().strip('"').strip("'")
        field_val = _get_field(field_path, payload)
        if field_val is None:
            return False
        if isinstance(field_val, list):
            return val_expr in field_val
        return val_expr in str(field_val)

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
        self._subscriptions = []
        self._emit_channel: str = ""
        self._emit_priority: Priority = Priority.HIGH

    async def setup(self) -> None:
        self._rules = self.spec.rules
        if self.spec.emit:
            self._emit_channel = self.spec.emit.channel
            self._emit_priority = Priority[self.spec.emit.priority.upper()]
        self._subscriptions = [
            self.spine.subscribe(ch) for ch in self.spec.subscribe_channels
        ]

    async def teardown(self) -> None:
        for sub in self._subscriptions:
            sub.close()

    async def run(self) -> None:
        if not self._subscriptions:
            await asyncio.sleep(1)
            return

        tasks = [asyncio.create_task(sub.get()) for sub in self._subscriptions]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()

        packet: IntelPacket = next(iter(done)).result()
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
