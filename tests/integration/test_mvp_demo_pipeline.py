"""Full-pipeline shakedown: run nexus/apparatuses/mvp-demo.nx end to end, offline.

Loads the real apparatus file through the real Hub, mocks the two RSS feeds
with respx and the LLM at AnthropicProvider.complete, and checks that packets
flow through every reachable stage, every node stays healthy, and shutdown is
clean.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from xml.sax.saxutils import escape

import fakeredis
import httpx
import pytest

from probable_intel.hub.hub import Hub
from probable_intel.llm.anthropic_provider import AnthropicProvider
from probable_intel.nodes.counterintel.fingerprint_node import FingerprintDefenseNode
from probable_intel.spine.redis_spine import RedisSpine

MVP_DEMO = Path(__file__).resolve().parents[2] / "nexus" / "apparatuses" / "mvp-demo.nx"
NARRATIVE_TEXT = "SHAKEDOWN NARRATIVE"

# FeedNode filters on the description only: >= 50 words AND a keyword substring.
# Per feed, 3 items pass; KREBS "k4" is too short and THN "t4" has no keyword.
# k1, k2, t1 score VADER < -0.97 (CRITICAL); the rest are neutral/positive.
KREBS_ITEMS = {
    "k1": "A devastating ransomware attack crippled hospital networks across three states this week, encrypting patient records and forcing emergency rooms to turn away ambulances. Attackers stole sensitive data and threatened to leak it publicly unless a massive ransom is paid. Investigators describe the breach as one of the most damaging and destructive intrusions ever seen in the healthcare sector, with lasting harm to patients.",
    "k2": "Criminals are actively exploiting a critical flaw in widely deployed VPN appliances to break into corporate networks, steal credentials, and install malicious backdoors. Security teams report widespread compromise, failed defenses, and serious losses at victim organizations. Researchers warn the exploit is trivial to abuse and that unpatched systems remain dangerously exposed to further attacks in the coming weeks.",
    "k3": "The vendor released its quarterly update addressing a vulnerability in the configuration interface of its storage product line. Administrators can install the update through the standard management console, and the release notes describe the affected versions, the steps for applying the patch, and the expected maintenance window. Documentation for the new version is available on the support portal today.",
    "k4": "Brief note: a new CVE was published today.",
}
THN_ITEMS = {
    "t1": "A zero-day flaw in a popular browser is being abused in targeted attacks against journalists and activists, allowing hackers to silently hijack devices, spy on private conversations, and steal sensitive files. Victims face serious danger, and experts warn the threat is severe, widespread, and extremely difficult to detect before damage is done to their organizations.",
    "t2": "CVE-2026-1234 describes an issue in a logging library used by many enterprise applications. The maintainers published version 4.2.1 with a fix, along with guidance on how to identify affected deployments and upgrade them. Teams are encouraged to review their dependency inventories and schedule the upgrade as part of their regular maintenance cycle this month.",
    "t3": "A retailer disclosed a data breach affecting customer loyalty accounts after an internal review found unauthorized access to a marketing database. The company says payment details were not involved, has reset affected passwords, and is offering free credit monitoring. Regulators have been notified, and the retailer says it continues to cooperate with investigators on the matter.",
    "t4": "The annual developer conference opened this morning with announcements about new programming tools, improved documentation, and a refreshed community forum. Speakers highlighted collaboration between open source maintainers and companies, and the organizers thanked volunteers for running workshops. Attendees can watch recorded sessions online, join evening meetups, and explore the expo floor throughout the week.",
}

# Log fragments that mean a stage failed even when no exception escaped
# (fetch errors, for one, are swallowed into the circuit breaker).
FAILURE_MARKERS = (
    "fetch failed", "run error", "setup failed", "LLM call failed",
    "directive apply failed", "save error", "failed to stop",
)

WATCHED = (
    "raw.feed.security", "analysis.sentiment.security", "threat.security",
    "sink.alerts", "system.task.directives", "analysis.narrative.security",
    "ci.attribution.events", "ci.opsec.audit",
)


# Every backend runs the identical pipeline; set REDIS_URL to include a real Redis.
SPINE_BACKENDS = ["memory", "fakeredis"] + (["redis"] if os.environ.get("REDIS_URL") else [])


@pytest.fixture(params=SPINE_BACKENDS)
def hub(request) -> Hub:
    h = Hub()
    if request.param == "fakeredis":
        server = fakeredis.FakeServer()
        h.spine = RedisSpine(
            "redis://fake",
            client=fakeredis.FakeAsyncRedis(server=server),
            control_client=fakeredis.FakeRedis(server=server),
            idle_sleep=0.02,
        )
    elif request.param == "redis":
        h.spine = RedisSpine(os.environ["REDIS_URL"], block_ms=200)
    return h  # mvp-demo has no `spine:` block, so it joins whichever spine this is


def _rss(channel_title: str, items: dict[str, str]) -> bytes:
    entries = "".join(
        f"<item><title>{key}</title><link>https://example.test/{key}</link>"
        f"<description>{escape(text)}</description></item>"
        for key, text in items.items()
    )
    return (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        f"<title>{channel_title}</title><link>https://example.test</link>"
        f"<description>fixture</description>{entries}</channel></rss>"
    ).encode()


async def _collect(sub, n: int, timeout: float = 10.0) -> list:
    return [await asyncio.wait_for(sub.get(), timeout) for _ in range(n)]


async def _nothing_more(sub, wait: float = 0.5) -> bool:
    try:
        await asyncio.wait_for(sub.get(), wait)
    except asyncio.TimeoutError:
        return True
    return False


async def test_mvp_demo_pipeline_end_to_end(hub, tmp_path, monkeypatch, respx_mock, caplog):
    caplog.set_level(logging.INFO)
    # The anthropic SDK (>=1.x) ships vendored httpx2/httpcore2, which respx cannot
    # intercept, so mock at our provider boundary; the SDK client is never built.
    llm_prompts: list[str] = []

    async def fake_complete(self, prompt: str, system: str = "", max_tokens: int = 1024) -> str:
        llm_prompts.append(prompt)
        return NARRATIVE_TEXT

    monkeypatch.setattr(AnthropicProvider, "complete", fake_complete)
    # Keep the real per-request jitter path but make it instant.
    monkeypatch.setattr(FingerprintDefenseNode, "_lognormal_delay", lambda self: 0.0)
    respx_mock.get("https://krebsonsecurity.com/feed/").mock(
        return_value=httpx.Response(200, content=_rss("Krebs", KREBS_ITEMS))
    )
    respx_mock.get("https://feeds.feedburner.com/TheHackersNews").mock(
        return_value=httpx.Response(200, content=_rss("THN", THN_ITEMS))
    )

    hub.load_apparatus(MVP_DEMO)
    registry = hub._registry
    assert len(registry.all_ids()) == 15

    # Redirect side-effect paths and speed up KG summaries; nodes read config in setup().
    db_path = tmp_path / "intel.db"
    kg_path = tmp_path / "kg.json"
    registry.get("archivist.storage.main").spec.config["path"] = str(db_path)
    kg_config = registry.get("kg.entities.security").spec.config
    kg_config["persist_path"] = str(kg_path)
    kg_config["emit_interval_seconds"] = 0

    subs = {ch: hub.spine.subscribe(ch) for ch in WATCHED}
    task = asyncio.create_task(hub.run())
    try:
        # Every outbound harvester request is reported to FingerprintDefenseNode,
        # which audits it to OpSecNode on ci.opsec.audit.
        audits = await _collect(subs["ci.opsec.audit"], 2)
        assert {a.payload["target_url"] for a in audits} == {
            "https://krebsonsecurity.com/feed/", "https://feeds.feedburner.com/TheHackersNews",
        }
        assert {a.payload["identity_profile_id"] for a in audits} == {"feed.krebs-security/rotate"}
        assert registry.get("feed.krebs-security")._request_guard is registry.get("ci.fingerprint.main")

        # Feed: 6 of 8 items pass the filters.
        await _collect(subs["raw.feed.security"], 6)
        assert await _nothing_more(subs["raw.feed.security"])

        sentiment = await _collect(subs["analysis.sentiment.security"], 6)
        assert all(isinstance(p.payload["sentiment_score"], float) for p in sentiment)

        # The 3 strongly negative items each trip the CRITICAL rule; neutral ones don't.
        threats = await _collect(subs["threat.security"], 3)
        assert {t.payload["max_severity"] for t in threats} == {"CRITICAL"}
        assert await _nothing_more(subs["threat.security"])

        alerts = await _collect(subs["sink.alerts"], 3)
        assert {a.packet_type for a in alerts} == {"AlertPacket"}

        directives = await _collect(subs["system.task.directives"], 1)
        assert directives[0].packet_type == "TaskDirectivePacket"

        narrative = await _collect(subs["analysis.narrative.security"], 1)
        assert narrative[0].payload["summary"] == NARRATIVE_TEXT

        # Counter-intel: two honeypot hits from one IP produce an attribution profile.
        deception = registry.get("ci.deception.main")
        for _ in range(2):
            await deception.trigger(
                "honeypot-api-01", requestor_ip="10.0.0.1", path="/api/v1/internal/nodes"
            )
        await _collect(subs["ci.attribution.events"], 1)

        # Honeypot hits are the highest-signal event: they must reach the alert sink.
        deception_alerts = []
        while not deception_alerts:
            alert = await asyncio.wait_for(subs["sink.alerts"].get(), 5)
            if alert.payload["source_node"] == "ci.deception.main":
                deception_alerts.append(alert)
        assert deception_alerts[0].payload["severity"] == "CRITICAL"

        await asyncio.sleep(0.5)  # let storage and the directive loop drain
        assert hub.status()["active_directives"] >= 1
        assert registry.get("ci.attribution.main")._kg_node is registry.get("kg.entities.security")
        assert len(llm_prompts) == 1

        unhealthy = [
            h for h in hub.status()["nodes"]
            if h["state"] != "RUNNING" or h["error_count"]
        ]
        assert unhealthy == []
        assert registry.get("feed.krebs-security")._target_errors == {}
    finally:
        task.cancel()
        await asyncio.wait_for(task, 10)
        for sub in subs.values():
            sub.close()

    failures = [
        r.getMessage() for r in caplog.records
        if any(marker in r.getMessage() for marker in FAILURE_MARKERS)
    ]
    assert failures == []

    with closing(sqlite3.connect(db_path)) as conn:
        counts = dict(conn.execute(
            "SELECT packet_type, COUNT(*) FROM intel_packets GROUP BY packet_type"
        ).fetchall())
    assert counts.get("SentimentPacket") == 6
    assert counts.get("EntityPacket") == 6
    assert counts.get("ThreatPacket") == 3
    assert counts.get("NarrativePacket") == 1
    assert counts.get("GraphSummaryPacket", 0) >= 1
    assert counts.get("AttributionPacket", 0) >= 1
    assert kg_path.exists()  # KG persisted on teardown
