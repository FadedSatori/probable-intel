from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from ..base import BaseNode
from ...spine.packet import IntelPacket, Priority

if TYPE_CHECKING:
    from ...nexus.spec import NodeSpec
    from ...spine.spine import Spine

log = logging.getLogger(__name__)

# (technique_id, name, tactics, keywords)
# Keyword heuristics, not a full ATT&CK dataset. Extend per-apparatus with the
# `extra_techniques` config key.
_BUILTIN_TECHNIQUES: list[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = [
    ("T1566", "Phishing", ("initial-access",),
     ("phishing", "spearphishing", "spear-phishing", "spear phishing")),
    ("T1190", "Exploit Public-Facing Application", ("initial-access",),
     ("public-facing", "remote code execution", "sql injection", "unauthenticated rce")),
    ("T1195", "Supply Chain Compromise", ("initial-access",),
     ("supply chain attack", "supply-chain attack", "supply chain compromise", "poisoned package")),
    ("T1189", "Drive-by Compromise", ("initial-access",),
     ("drive-by", "watering hole")),
    ("T1133", "External Remote Services", ("initial-access", "persistence"),
     ("exposed rdp", "exposed vpn", "vpn appliance", "vpn gateway")),
    ("T1078", "Valid Accounts",
     ("initial-access", "persistence", "privilege-escalation", "defense-evasion"),
     ("stolen credentials", "compromised credentials", "credential stuffing", "leaked credentials")),
    ("T1059", "Command and Scripting Interpreter", ("execution",),
     ("powershell", "malicious script", "command-line interpreter")),
    ("T1204", "User Execution", ("execution",),
     ("malicious attachment", "malicious document", "malicious link")),
    ("T1068", "Exploitation for Privilege Escalation", ("privilege-escalation",),
     ("privilege escalation", "local privilege")),
    ("T1027", "Obfuscated Files or Information", ("defense-evasion",),
     ("obfuscated", "obfuscation")),
    ("T1562", "Impair Defenses", ("defense-evasion",),
     ("disable antivirus", "disabled security tools", "edr evasion", "tamper protection")),
    ("T1110", "Brute Force", ("credential-access",),
     ("brute force", "brute-force", "password spraying")),
    ("T1003", "OS Credential Dumping", ("credential-access",),
     ("mimikatz", "credential dumping", "lsass")),
    ("T1539", "Steal Web Session Cookie", ("credential-access",),
     ("session cookie", "cookie theft", "stolen cookies")),
    ("T1056", "Input Capture", ("collection", "credential-access"),
     ("keylogger", "keylogging")),
    ("T1021", "Remote Services", ("lateral-movement",),
     ("lateral movement",)),
    ("T1071", "Application Layer Protocol", ("command-and-control",),
     ("command and control", "c2 server", "c2 infrastructure")),
    ("T1105", "Ingress Tool Transfer", ("command-and-control",),
     ("downloads additional payload", "second-stage payload", "tool transfer")),
    ("T1041", "Exfiltration Over C2 Channel", ("exfiltration",),
     ("data exfiltration", "exfiltrated data", "exfiltrate")),
    ("T1486", "Data Encrypted for Impact", ("impact",),
     ("ransomware", "encrypted files", "files encrypted")),
    ("T1485", "Data Destruction", ("impact",),
     ("wiper", "data destruction", "destructive malware")),
    ("T1490", "Inhibit System Recovery", ("impact",),
     ("delete backups", "deleted backups", "shadow copies", "volume shadow")),
    ("T1498", "Network Denial of Service", ("impact",),
     ("ddos", "denial of service", "denial-of-service")),
    ("T1657", "Financial Theft", ("impact",),
     ("business email compromise", "wire fraud", "payment fraud")),
]

_DEFAULT_TEXT_FIELDS = ("title", "content", "summary")


def _compile(keywords: Sequence[str]) -> re.Pattern[str]:
    alts = "|".join(re.escape(k.lower()) for k in sorted(keywords, key=len, reverse=True))
    return re.compile(rf"(?<![\w-])(?:{alts})(?![\w-])", re.IGNORECASE)


class AttackMapperNode(BaseNode):
    """Tags packets with MITRE ATT&CK techniques via keyword heuristics.

    Scans the packet's text fields for technique keywords and emits an
    AttackMappingPacket carrying the original payload plus:
        attack_techniques     [{technique_id, name, tactics, matched_keywords}]
        attack_technique_ids  ["T1566", ...]

    Packets with no match are dropped. Matching is a heuristic, so each match
    is reported with the keywords that triggered it.

    Config keys (all optional):
        text_fields        Payload fields to scan (default title, content, summary)
        extra_techniques   List of {id, name, tactics, keywords} to add or
                           override built-in entries (matched by id)
    """

    def __init__(self, spec: "NodeSpec", spine: "Spine") -> None:
        super().__init__(spec, spine)
        self._subscriptions: list = []
        self._emit_channel: str = ""
        self._emit_priority: Priority = Priority.NORMAL
        self._text_fields: tuple[str, ...] = _DEFAULT_TEXT_FIELDS
        self._techniques: dict[str, dict[str, Any]] = {}

    async def setup(self) -> None:
        cfg = self.spec.config
        self._text_fields = tuple(cfg.get("text_fields", _DEFAULT_TEXT_FIELDS))

        table = {
            tid: {"name": name, "tactics": list(tactics), "keywords": list(kws)}
            for tid, name, tactics, kws in _BUILTIN_TECHNIQUES
        }
        for extra in cfg.get("extra_techniques", []):
            tid = str(extra.get("id", "")).strip()
            kws = [str(k) for k in extra.get("keywords", [])]
            if not tid or not kws:
                log.warning("node %s: skipping malformed extra_technique %r", self.node_id, extra)
                continue
            table[tid] = {
                "name": str(extra.get("name", tid)),
                "tactics": [str(t) for t in extra.get("tactics", [])],
                "keywords": kws,
            }
        self._techniques = {
            tid: {**t, "pattern": _compile(t["keywords"])} for tid, t in table.items()
        }

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
        await self._map(packet)

    def _scan(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        text = "\n".join(str(payload.get(f, "")) for f in self._text_fields)
        if not text.strip():
            return []
        found = []
        for tid, t in self._techniques.items():
            hits = sorted({m.group(0).lower() for m in t["pattern"].finditer(text)})
            if hits:
                found.append({
                    "technique_id": tid,
                    "name": t["name"],
                    "tactics": t["tactics"],
                    "matched_keywords": hits,
                })
        return found

    async def _map(self, packet: IntelPacket) -> None:
        techniques = self._scan(packet.payload)
        if not techniques or not self._emit_channel:
            return

        ids = [t["technique_id"] for t in techniques]
        out = packet.relay(
            self.node_id,
            self._emit_channel,
            packet_type="AttackMappingPacket",
            payload={
                **packet.payload,
                "attack_techniques": techniques,
                "attack_technique_ids": ids,
            },
            priority=max(packet.priority, self._emit_priority),
            tags=list(dict.fromkeys([*packet.tags, *ids])),
        )
        await self.emit(self._emit_channel, out)
        log.info(
            "node %s: mapped %d ATT&CK technique(s) %s in packet %s",
            self.node_id, len(ids), ids, packet.packet_id,
        )
