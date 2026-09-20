"""
Moteur d'identification de la cause racine (Ticket RCA, critères 3 et 4).

Principe : parmi toutes les entités en alerte dans un même incident corrélé
(cf. alert_correlator.py), la cause racine la plus probable est celle qui
est LA PLUS EN AMONT dans le graphe de dépendances — c'est-à-dire celle
dont aucune dépendance directe n'est elle-même en alerte. Si B est en panne
et que A (qui dépend de B) est aussi en alerte, B est plus probablement
la cause que A (A subit probablement les conséquences de B).
"""

from dataclasses import dataclass, field

from alert_correlator import IncidentGroup
from service_graph import ServiceGraph


@dataclass
class RCAResult:
    incident_group: IncidentGroup
    root_cause_entity: str
    root_cause_type: str          # "service" | "infra" | "network" | "unknown"
    confidence_percent: int
    correlated_alert_count: int
    explained_entities: set[str] = field(default_factory=set)    # cohérentes avec le root cause
    unexplained_entities: set[str] = field(default_factory=set)  # pas expliquées par la propagation -> à surveiller

    def to_dict(self) -> dict:
        return {
            "root_cause_entity": self.root_cause_entity,
            "root_cause_type": self.root_cause_type,
            "confidence_percent": self.confidence_percent,
            "correlated_alert_count": self.correlated_alert_count,
            "affected_entities": sorted(self.incident_group.entities),
            "explained_by_root_cause": sorted(self.explained_entities),
            "unexplained_entities": sorted(self.unexplained_entities),
            "incident_start": self.incident_group.start_time.isoformat(),
            "incident_end": self.incident_group.end_time.isoformat(),
        }


class RCAEngine:
    def __init__(self, graph: ServiceGraph):
        self.graph = graph

    def analyze(self, group: IncidentGroup) -> RCAResult:
        entities = group.entities

        # Candidats = entités sans dépendance directe elle-même en alerte
        # (donc "les plus en amont" du groupe)
        candidates = [e for e in entities if not self.graph.has_upstream_within(e, entities)]

        if not candidates:
            # Cas dégénéré (ex: cycle dans le graphe, ou entité inconnue) :
            # on retombe sur l'alerte la plus ancienne du groupe, par défaut.
            candidates = list(entities)

        root_cause = self._earliest_alert_entity(group, candidates)

        # Vérifie quelles autres entités du groupe sont cohérentes avec
        # cette cause racine (elles sont en aval dans le graphe), et
        # lesquelles ne le sont pas (root cause potentiellement incomplète
        # ou incident multiple simultané -> signalé, pas caché).
        downstream = self.graph.propagate_impact(root_cause)
        explained = {root_cause} | (downstream & entities)
        unexplained = entities - explained

        confidence = round(100 * len(explained) / len(entities)) if entities else 0

        return RCAResult(
            incident_group=group,
            root_cause_entity=root_cause,
            root_cause_type=self.graph.entity_type(root_cause),
            confidence_percent=confidence,
            correlated_alert_count=len(group.alerts),
            explained_entities=explained,
            unexplained_entities=unexplained,
        )

    @staticmethod
    def _earliest_alert_entity(group: IncidentGroup, candidates: list[str]) -> str:
        """Parmi les candidats, celui dont l'alerte est arrivée en premier
        (la propagation d'un incident prend du temps : la cause racine
        déclenche généralement l'alerte la plus précoce)."""
        candidate_alerts = [a for a in group.alerts if a.entity in candidates]
        earliest = min(candidate_alerts, key=lambda a: a.timestamp)
        return earliest.entity
