from __future__ import annotations

from typing import TYPE_CHECKING

from ..nodes.base import BaseNode
from ..nexus.errors import NEXUSError

if TYPE_CHECKING:
    from ..nexus.spec import NodeSpec
    from ..spine.spine import Spine


def _lazy_imports() -> dict[str, type[BaseNode]]:
    from ..nodes.harvesters.feed_node import FeedNode
    from ..nodes.harvesters.web_node import WebNode
    from ..nodes.harvesters.api_node import ApiNode
    from ..nodes.harvesters.social_node import SocialNode
    from ..nodes.analysts.sentiment_node import SentimentNode
    from ..nodes.analysts.entity_node import EntityExtractorNode
    from ..nodes.analysts.threat_node import ThreatAssessNode
    from ..nodes.sentinels.alert_node import AlertNode
    from ..nodes.sentinels.anomaly_node import AnomalyNode
    from ..nodes.archivists.storage_node import StorageNode
    from ..nodes.archivists.kg_node import KnowledgeGraphNode
    from ..nodes.counterintel.opsec_node import OpSecNode
    from ..nodes.counterintel.deception_node import DeceptionNode
    from ..nodes.counterintel.fingerprint_node import FingerprintDefenseNode

    return {
        "FeedNode": FeedNode,
        "WebNode": WebNode,
        "ApiNode": ApiNode,
        "SocialNode": SocialNode,
        "SentimentNode": SentimentNode,
        "EntityExtractorNode": EntityExtractorNode,
        "ThreatAssessNode": ThreatAssessNode,
        "AlertNode": AlertNode,
        "AnomalyNode": AnomalyNode,
        "StorageNode": StorageNode,
        "KnowledgeGraphNode": KnowledgeGraphNode,
        "OpSecNode": OpSecNode,
        "DeceptionNode": DeceptionNode,
        "FingerprintDefenseNode": FingerprintDefenseNode,
    }


class NodeFactory:
    def __init__(self) -> None:
        self._registry: dict[str, type[BaseNode]] | None = None

    def _get_registry(self) -> dict[str, type[BaseNode]]:
        if self._registry is None:
            self._registry = _lazy_imports()
        return self._registry

    def create(self, spec: "NodeSpec", spine: "Spine") -> BaseNode:
        registry = self._get_registry()
        cls = registry.get(spec.node_type)
        if cls is None:
            raise NEXUSError(
                f"unknown node type {spec.node_type!r}; "
                f"available: {sorted(registry.keys())}",
                apparatus_name=spec.apparatus_id,
            )
        return cls(spec, spine)
