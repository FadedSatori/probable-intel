from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..base import BaseNode
from ...spine.packet import IntelPacket, Priority

if TYPE_CHECKING:
    from ...nexus.spec import NodeSpec
    from ...spine.spine import Spine

log = logging.getLogger(__name__)

# Namespace for deterministic STIX ids: the same input always yields the same id,
# so re-exporting a packet or seeing the same CVE twice never creates duplicates.
_NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/FadedSatori/probable-intel")

_CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,}\b", re.IGNORECASE)


def _sid(stix_type: str, key: str) -> str:
    return f"{stix_type}--{uuid.uuid5(_NS, f'{stix_type}:{key}')}"


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


class StixExportNode(BaseNode):
    """Converts analysed packets into STIX 2.1 bundles.

    Each packet that references at least one CVE, ATT&CK technique or
    organisation becomes a bundle containing:
        identity          the producing apparatus (created_by_ref on everything)
        vulnerability     one per CVE (from CVE entities and CVE ids in text)
        attack-pattern    one per technique in `attack_techniques`
        identity          one per ORG entity (identity_class=organization)
        report            ties the above together via object_refs

    Object ids are deterministic (uuid5), and the bundle file is named after the
    packet's source_hash, so re-exporting the same item overwrites rather than
    duplicates. Packets that reference nothing are skipped (a STIX report needs
    at least one object_ref).

    Config keys (all optional):
        export_dir      Directory to write <source_hash>.json bundles into
        identity_name   Name for the producer identity (default: apparatus id)
    """

    def __init__(self, spec: "NodeSpec", spine: "Spine") -> None:
        super().__init__(spec, spine)
        self._subscriptions: list = []
        self._emit_channel: str = ""
        self._emit_priority: Priority = Priority.NORMAL
        self._export_dir: Path | None = None
        self._identity_name: str = ""

    async def setup(self) -> None:
        cfg = self.spec.config
        if cfg.get("export_dir"):
            self._export_dir = Path(cfg["export_dir"])
            self._export_dir.mkdir(parents=True, exist_ok=True)
        self._identity_name = str(cfg.get("identity_name") or self.spec.apparatus_id)

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
        await self._export(packet)

    # ── bundle construction ───────────────────────────────────────────────

    def build_bundle(self, packet: IntelPacket) -> dict[str, Any] | None:
        """Return a STIX 2.1 bundle for the packet, or None if it references nothing."""
        payload = packet.payload
        now = _ts(packet.timestamp_utc)
        producer_id = _sid("identity", f"producer:{packet.apparatus_id}")

        def sdo(stix_type: str, key: str, **props: Any) -> dict[str, Any]:
            return {
                "type": stix_type,
                "spec_version": "2.1",
                "id": _sid(stix_type, key),
                "created": now,
                "modified": now,
                "created_by_ref": producer_id,
                **props,
            }

        refs: list[str] = []
        objects: list[dict[str, Any]] = []

        # CVEs: entities plus ids found in free text
        cves: dict[str, None] = {}
        for ent in payload.get("entities", []):
            if str(ent.get("type", "")).upper() == "CVE":
                m = _CVE_RE.search(str(ent.get("text", "")))
                if m:
                    cves[m.group(0).upper()] = None
        text = "\n".join(str(payload.get(f, "")) for f in ("title", "content", "summary"))
        for m in _CVE_RE.finditer(text):
            cves[m.group(0).upper()] = None
        for cve in cves:
            obj = sdo(
                "vulnerability", cve, name=cve,
                external_references=[{"source_name": "cve", "external_id": cve}],
            )
            objects.append(obj)
            refs.append(obj["id"])

        # ATT&CK techniques
        for tech in payload.get("attack_techniques", []):
            tid = str(tech.get("technique_id", "")).strip()
            if not tid:
                continue
            path = tid.replace(".", "/")
            ap_props: dict[str, Any] = {
                "name": str(tech.get("name", tid)),
                "external_references": [{
                    "source_name": "mitre-attack",
                    "external_id": tid,
                    "url": f"https://attack.mitre.org/techniques/{path}/",
                }],
            }
            phases = [
                {"kill_chain_name": "mitre-attack", "phase_name": str(t)}
                for t in tech.get("tactics", [])
            ]
            if phases:  # STIX requires at least one entry when the property is present
                ap_props["kill_chain_phases"] = phases
            obj = sdo("attack-pattern", tid, **ap_props)
            objects.append(obj)
            refs.append(obj["id"])

        # Organisations
        orgs: dict[str, None] = {}
        for ent in payload.get("entities", []):
            if str(ent.get("type", "")).upper() == "ORG":
                name = str(ent.get("text", "")).strip()
                if name:
                    orgs[name] = None
        for name in orgs:
            obj = sdo("identity", f"org:{name.lower()}", name=name, identity_class="organization")
            objects.append(obj)
            refs.append(obj["id"])

        if not refs:
            return None

        title = str(payload.get("title") or f"Intel report {packet.source_hash}")
        labels = [m["label"] for m in payload.get("threat_matches", []) if m.get("label")]
        report_props: dict[str, Any] = {
            "name": title,
            "published": now,
            "report_types": ["threat-report"],
            "object_refs": refs,
            "confidence": max(0, min(100, round(packet.confidence * 100))),
            "x_source_hash": packet.source_hash,
        }
        if labels:
            report_props["labels"] = list(dict.fromkeys(labels))
        url = payload.get("url") or payload.get("link")
        if url:
            report_props["external_references"] = [
                {"source_name": self._identity_name, "url": str(url)}
            ]
        report = sdo("report", packet.source_hash, **report_props)

        producer = {
            "type": "identity",
            "spec_version": "2.1",
            "id": producer_id,
            "created": now,
            "modified": now,
            "name": self._identity_name,
            "identity_class": "system",
        }
        return {
            "type": "bundle",
            "id": _sid("bundle", packet.source_hash),
            "objects": [producer, *objects, report],
        }

    async def _export(self, packet: IntelPacket) -> None:
        bundle = self.build_bundle(packet)
        if bundle is None:
            return

        export_path = ""
        if self._export_dir is not None:
            path = self._export_dir / f"{packet.source_hash}.json"
            path.write_text(json.dumps(bundle, indent=2, sort_keys=True), encoding="utf-8")
            export_path = str(path)

        if self._emit_channel:
            out = packet.relay(
                self.node_id,
                self._emit_channel,
                packet_type="StixBundlePacket",
                payload={
                    "bundle": bundle,
                    "object_count": len(bundle["objects"]),
                    "export_path": export_path,
                },
                priority=self._emit_priority,
            )
            await self.emit(self._emit_channel, out)
        log.info(
            "node %s: exported STIX bundle with %d object(s) for packet %s",
            self.node_id, len(bundle["objects"]), packet.packet_id,
        )
