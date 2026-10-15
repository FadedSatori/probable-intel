"""Unit tests for BaseNode helpers."""
from __future__ import annotations

import asyncio

from probable_intel.nexus.spec import NodeSpec
from probable_intel.nodes.base import BaseNode
from probable_intel.spine.packet import IntelPacket
from probable_intel.spine.spine import Spine


class _Node(BaseNode):
    async def run(self) -> None:
        await asyncio.sleep(0)


def _packet(channel: str, i: int) -> IntelPacket:
    return IntelPacket(
        packet_type="TestPacket",
        source_node_id="test.source",
        apparatus_id="test",
        channel=channel,
        payload={"i": i},
    )


async def test_wait_any_delivers_every_packet_across_busy_channels():
    """Packets arriving on several channels in the same tick must not be dropped."""
    spine = Spine()
    node = _Node(NodeSpec(node_type="Test", node_id="test.node", apparatus_id="test"), spine)
    subs = [spine.subscribe("chan.a"), spine.subscribe("chan.b"), spine.subscribe("chan.c")]
    for i in range(50):
        for ch in ("chan.a", "chan.b", "chan.c"):
            await spine.publish(ch, _packet(ch, i))

    received = []
    while True:
        try:
            received.append(await asyncio.wait_for(node._wait_any(subs), timeout=0.2))
        except asyncio.TimeoutError:
            break

    assert len(received) == 150
    assert len({(p.channel, p.payload["i"]) for p in received}) == 150
    for ch in ("chan.a", "chan.b", "chan.c"):
        order = [p.payload["i"] for p in received if p.channel == ch]
        assert order == list(range(50))  # per-channel order preserved


class _InstantSub:
    def __init__(self, packet: IntelPacket) -> None:
        self.packet = packet

    async def get(self) -> IntelPacket:
        return self.packet


class _IgnoresCancelSub:
    """Like a network client that finishes its read even after being cancelled."""

    def __init__(self, packet: IntelPacket) -> None:
        self.packet = packet

    async def get(self) -> IntelPacket:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            pass
        return self.packet


async def test_wait_any_keeps_packets_from_reads_that_ignore_cancellation():
    node = _Node(NodeSpec(node_type="Test", node_id="test.node", apparatus_id="test"), Spine())
    fast, stubborn = _packet("chan.fast", 1), _packet("chan.slow", 2)

    first = await asyncio.wait_for(node._wait_any([_InstantSub(fast), _IgnoresCancelSub(stubborn)]), 1.0)

    assert first is fast
    assert await node._wait_any([]) is stubborn  # salvaged, not dropped
