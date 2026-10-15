"""Static MITRE ATT&CK lookup (nexus/data/attack_mapping.yaml) for ThreatAssessNode."""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "nexus" / "data" / "attack_mapping.yaml"
_TECHNIQUE_ID = re.compile(r"^T\d{4}(\.\d{3})?$")


@dataclass(frozen=True)
class Technique:
    id: str
    name: str
    tactics: tuple[str, ...]

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "tactics": list(self.tactics)}


class AttackMapping:
    def __init__(
        self,
        techniques: dict[str, Technique],
        keywords: dict[str, list[str]],
        labels: dict[str, list[str]],
    ) -> None:
        for tid in techniques:
            if not _TECHNIQUE_ID.match(tid):
                raise ValueError(f"invalid ATT&CK technique id {tid!r}")
        for source, mapping in (("keywords", keywords), ("labels", labels)):
            for key, ids in mapping.items():
                unknown = [t for t in ids if t not in techniques]
                if unknown:
                    raise ValueError(f"{source}[{key!r}] references unknown technique(s) {unknown}")
        self._techniques = techniques
        self._labels = labels
        # Whole word/phrase: "phishing" must not match inside "antiphishingtool".
        self._keywords = [
            (re.compile(rf"(?<!\w){re.escape(kw)}(?!\w)", re.IGNORECASE), ids)
            for kw, ids in keywords.items()
        ]

    @classmethod
    def load(cls, path: Path | str = DEFAULT_PATH) -> "AttackMapping":
        data = yaml.safe_load(Path(path).read_text()) or {}
        techniques = {
            str(tid): Technique(str(tid), str(t["name"]), tuple(str(x) for x in t["tactics"]))
            for tid, t in (data.get("techniques") or {}).items()
        }
        return cls(
            techniques,
            {str(k): [str(t) for t in v] for k, v in (data.get("keywords") or {}).items()},
            {str(k): [str(t) for t in v] for k, v in (data.get("labels") or {}).items()},
        )

    def match(self, labels: Iterable[str], text: str) -> list[dict]:
        """Techniques implied by rule labels and by keywords in text, de-duplicated."""
        ids: dict[str, None] = {}
        for label in labels:
            ids.update(dict.fromkeys(self._labels.get(label, [])))
        for pattern, technique_ids in self._keywords:
            if pattern.search(text):
                ids.update(dict.fromkeys(technique_ids))
        return [self._techniques[tid].to_dict() for tid in ids]
