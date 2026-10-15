"""Unit tests for AlertNode."""
from __future__ import annotations

import asyncio
import json

import pytest

from probable_intel.nexus.spec import EmitSpec, NodeSpec
from probable_intel.nodes.sentinels.alert_node import AlertNode
from probable_intel.spine.packet import IntelPacket
from probable_intel.spine.spine import Spine


def _make_node(spine: Spine, **config) -> AlertNode:
    spec = NodeSpec(
        node_type="AlertNode",
        node_id="sentinel.alert.test",
        apparatus_id="test",
        subscribe_channels=["threat.test"],
        emit=EmitSpec(channel="sink.alerts", priority="critical"),
        config=config,
    )
    return AlertNode(spec, spine)


def _threat(severity: str, source_hash: str = "hash-1") -> IntelPacket:
    return IntelPacket(
        packet_type="ThreatPacket",
        source_node_id="analyst.threat.test",
        apparatus_id="test",
        channel="threat.test",
        payload={
            "title": "Ransomware hits hospitals",
            "max_severity": severity,
            "threat_matches": [{"severity": severity, "label": "test-rule"}],
        },
        source_hash=source_hash,
    )


async def test_high_severity_threat_is_published_as_alert_packet():
    spine = Spine()
    node = _make_node(spine)
    await node.setup()
    alerts = spine.subscribe("sink.alerts")

    await node._handle(_threat("CRITICAL"))

    alert = await asyncio.wait_for(alerts.get(), timeout=1.0)
    assert alert.packet_type == "AlertPacket"
    assert alert.payload["severity"] == "CRITICAL"
    assert alert.payload["matches"] == [{"severity": "CRITICAL", "label": "test-rule"}]
    assert alert.provenance[0] == "sentinel.alert.test"
    await node.teardown()


async def test_low_severity_threat_is_ignored():
    spine = Spine()
    node = _make_node(spine)
    await node.setup()
    alerts = spine.subscribe("sink.alerts")

    await node._handle(_threat("LOW"))

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(alerts.get(), timeout=0.1)
    await node.teardown()


async def test_duplicate_source_hash_alerts_once():
    spine = Spine()
    node = _make_node(spine)
    await node.setup()
    alerts = spine.subscribe("sink.alerts")

    await node._handle(_threat("HIGH", source_hash="same"))
    await node._handle(_threat("HIGH", source_hash="same"))

    await asyncio.wait_for(alerts.get(), timeout=1.0)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(alerts.get(), timeout=0.1)
    await node.teardown()


async def test_alert_is_also_written_to_logfile(tmp_path):
    log_path = tmp_path / "alerts" / "alerts.jsonl"
    spine = Spine()
    node = _make_node(spine, log_path=str(log_path))
    await node.setup()

    await node._handle(_threat("HIGH"))

    line = log_path.read_text().strip().splitlines()[-1]
    assert json.loads(line)["severity"] == "HIGH"
    await node.teardown()


async def test_counterintel_severity_field_is_honoured():
    """Deception/OpSec/Attribution packets carry `severity`, not `max_severity`."""
    spine = Spine()
    node = _make_node(spine)
    await node.setup()
    alerts = spine.subscribe("sink.alerts")
    trigger = IntelPacket(
        packet_type="DeceptionTriggerPacket",
        source_node_id="ci.deception.test",
        apparatus_id="test",
        channel="ci.deception.triggers",
        payload={"canary_id": "canary-1", "severity": "CRITICAL"},
    )

    await node._handle(trigger)

    alert = await asyncio.wait_for(alerts.get(), timeout=1.0)
    assert alert.payload["severity"] == "CRITICAL"
    assert alert.payload["source_node"] == "ci.deception.test"
    await node.teardown()
