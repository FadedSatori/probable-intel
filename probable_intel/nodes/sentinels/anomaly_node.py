from __future__ import annotations

import logging
import math
import statistics
import time
from collections import defaultdict, deque
from typing import TYPE_CHECKING

from ..base import BaseNode
from ...spine.packet import IntelPacket, Priority
from ...nodes.analysts.threat_node import _eval_condition, _SEVERITY_RANK, _SEVERITY_PRIORITY

if TYPE_CHECKING:
    from ...nexus.spec import NodeSpec
    from ...spine.spine import Spine

log = logging.getLogger(__name__)


def _zscore(value: float, mean: float, stddev: float) -> float:
    return (value - mean) / stddev if stddev > 0 else 0.0


class AnomalyNode(BaseNode):
    """Sliding-window statistical anomaly detector. Emits AnomalyPackets.

    Tracks per-channel baselines for packet volume and sentiment_score.
    Fires when z-scores exceed operator-defined rule thresholds.
    Suppresses output during the warm-up period (min_samples not yet reached).
    """

    def __init__(self, spec: "NodeSpec", spine: "Spine") -> None:
        super().__init__(spec, spine)
        self._subscriptions: list = []

        # Configuration
        self._window_seconds: int = 300
        self._sentiment_window_size: int = 100
        self._min_samples: int = 20

        # Per-channel state
        self._timestamps: dict[str, deque] = defaultdict(lambda: deque(maxlen=2000))
        self._volume_baseline: dict[str, deque] = defaultdict(lambda: deque(maxlen=100))
        self._last_window_flush: dict[str, float] = defaultdict(float)
        self._sentiment_scores: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=self._sentiment_window_size)
        )
        self._seen_count: dict[str, int] = defaultdict(int)

    async def setup(self) -> None:
        cfg = self.spec.config
        self._window_seconds = int(cfg.get("window_seconds", 300))
        self._sentiment_window_size = int(cfg.get("sentiment_window_size", 100))
        self._min_samples = int(cfg.get("min_samples", 20))

        self._subscriptions = [
            self.spine.subscribe(ch) for ch in self.spec.subscribe_channels
        ]

    async def teardown(self) -> None:
        for sub in self._subscriptions:
            sub.close()

    async def run(self) -> None:
        packet = await self._wait_any(self._subscriptions)
        if packet is None:
            return
        await self._analyze(packet)

    async def _analyze(self, packet: IntelPacket) -> None:
        channel = packet.channel
        now = time.time()

        # Record arrival
        self._timestamps[channel].append(now)
        self._seen_count[channel] += 1

        # Track sentiment if present
        score = packet.payload.get("sentiment_score")
        if score is not None:
            try:
                self._sentiment_scores[channel].append(float(score))
            except (TypeError, ValueError):
                pass

        # Flush volume window periodically
        last_flush = self._last_window_flush[channel]
        if now - last_flush >= self._window_seconds:
            current_window_count = sum(
                1 for ts in self._timestamps[channel]
                if ts >= now - self._window_seconds
            )
            self._volume_baseline[channel].append(current_window_count)
            self._last_window_flush[channel] = now

        # Suppress during warm-up
        if self._seen_count[channel] < self._min_samples:
            return
        if not self.spec.rules or not self._emit_channel:
            return

        metrics = self._compute_metrics(channel, now)
        await self._evaluate_rules(packet, channel, metrics)

    def _compute_metrics(self, channel: str, now: float) -> dict:
        # Volume z-score
        current_count = sum(
            1 for ts in self._timestamps[channel]
            if ts >= now - self._window_seconds
        )
        vb = list(self._volume_baseline[channel])
        if len(vb) >= 2:
            v_mean = statistics.mean(vb)
            v_std = statistics.stdev(vb)
            volume_z = _zscore(current_count, v_mean, v_std)
        else:
            v_mean = float(current_count)
            v_std = 0.0
            volume_z = 0.0

        # Sentiment z-score and volatility
        scores = list(self._sentiment_scores[channel])
        if len(scores) >= 2:
            s_mean = statistics.mean(scores)
            s_std = statistics.stdev(scores)
            latest_score = scores[-1]
            sentiment_z = _zscore(latest_score, s_mean, s_std)
            # Volatility: normalized stddev (0..1 range approximation)
            sentiment_volatility = min(s_std, 1.0)
        else:
            s_mean = 0.0
            s_std = 0.0
            sentiment_z = 0.0
            sentiment_volatility = 0.0

        return {
            "volume_z_score": round(volume_z, 3),
            "volume_count": current_count,
            "volume_baseline_mean": round(v_mean, 2),
            "volume_baseline_stddev": round(v_std, 3),
            "sentiment_z_score": round(sentiment_z, 3),
            "sentiment_volatility": round(sentiment_volatility, 3),
            "sentiment_mean": round(s_mean, 3),
            "sentiment_stddev": round(s_std, 3),
            "channel": channel,
        }

    async def _evaluate_rules(
        self, packet: IntelPacket, channel: str, metrics: dict
    ) -> None:
        matched = []
        for rule in self.spec.rules:
            try:
                if _eval_condition(rule.condition, metrics):
                    matched.append({"severity": rule.severity, "label": rule.label})
            except Exception as e:
                log.warning("node %s: rule eval error %r: %s", self.node_id, rule.condition, e)

        if not matched:
            return

        max_severity = max(matched, key=lambda m: _SEVERITY_RANK.get(m["severity"], 0))["severity"]
        out_priority = _SEVERITY_PRIORITY.get(max_severity, Priority.NORMAL)

        # Determine primary anomaly type from highest-severity rule label
        top_label = max(matched, key=lambda m: _SEVERITY_RANK.get(m["severity"], 0))["label"]
        anomaly_type = (
            "volume_spike" if "volume" in top_label
            else "sentiment_spike" if "sentiment" in top_label and "volatility" not in top_label
            else "sentiment_volatility" if "volatility" in top_label
            else "anomaly"
        )

        out = packet.relay(
            self.node_id,
            self._emit_channel,
            packet_type="AnomalyPacket",
            payload={
                "anomaly_type": anomaly_type,
                "channel": channel,
                "matched_rules": matched,
                "max_severity": max_severity,
                "sample_count": self._seen_count[channel],
                **metrics,
            },
            priority=out_priority,
        )
        await self.emit(self._emit_channel, out)
        log.info(
            "node %s: ANOMALY [%s] %s on channel %r (z=%.2f / vol_z=%.2f)",
            self.node_id,
            max_severity,
            anomaly_type,
            channel,
            metrics["sentiment_z_score"],
            metrics["volume_z_score"],
        )
