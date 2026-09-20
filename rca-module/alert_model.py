"""
Modèle d'alerte unifié.

Epic 1 (anomaly-detector) et Epic 2 (llm-log-analyzer) produisent chacun
un format d'événement différent. Le module RCA a besoin d'un format commun
pour pouvoir les corréler ensemble — c'est le rôle de cette petite couche
d'adaptation.
"""

from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class Alert:
    """Représentation unifiée d'une alerte, quelle que soit sa source."""
    timestamp: datetime
    source: str          # "epic1_metrics" ou "epic2_logs"
    entity: str          # nom résolu dans le graphe (service/infra/network)
    severity: str        # "warning" ou "critical"
    detail: str           # résumé lisible, pour affichage/debug
    raw: dict              # événement original, pour traçabilité complète


def from_epic1_event(event: dict, graph) -> "Alert | None":
    """
    Convertit un événement JSON du module anomaly-detector (Epic 1) en
    Alert unifiée. Le champ "node" est résolu en nom de service via le
    graphe de dépendances (service_graph.yaml, hosted_on). Si le nœud
    n'est pas mappé à un service connu, on garde le nœud lui-même comme
    entité (catégorie "infra" par défaut).
    """
    node = event.get("node")
    if not node:
        return None
    entity = graph.resolve_from_node(node) or node
    return Alert(
        timestamp=datetime.fromisoformat(event["timestamp"]),
        source="epic1_metrics",
        entity=entity,
        severity=event.get("severity", "warning"),
        detail=", ".join(m["name"] for m in event.get("metrics", [])),
        raw=event,
    )


def from_epic2_diagnostic(diagnostic: dict, timestamp: datetime | None = None) -> "Alert | None":
    """
    Convertit un diagnostic structuré du module llm-log-analyzer (Epic 2)
    en Alert unifiée. `affected_service` devient directement l'entité.
    """
    if not diagnostic.get("has_error"):
        return None
    entity = diagnostic.get("affected_service") or "unknown"
    severity = "critical" if diagnostic.get("confidence_percent", 0) >= 80 else "warning"
    return Alert(
        timestamp=timestamp or datetime.now(timezone.utc),
        source="epic2_logs",
        entity=entity,
        severity=severity,
        detail=diagnostic.get("error_category", "Unknown"),
        raw=diagnostic,
    )
