"""
Classification de sévérité et priorisation des incidents.

Prend le résultat du module RCA (rca_engine.py) et calcule :
  - un score de sévérité combinant impact business, rayon d'impact (blast
    radius) et durée de l'incident (critères 1 et 2)
  - une prédiction de dépassement de SLA, AVANT qu'il arrive (critère 3)
  - un tri de plusieurs incidents du plus urgent au moins urgent (critère 4)
"""

from dataclasses import dataclass
from datetime import datetime, timezone

import yaml

from rca_engine import RCAResult

# Poids relatifs des 3 facteurs dans le score final (somme = 1.0).
# Le business pèse le plus, car c'est le critère explicitement demandé
# en premier par le ticket ("impact business estimé").
WEIGHT_BUSINESS = 0.5
WEIGHT_BLAST_RADIUS = 0.3
WEIGHT_DURATION = 0.2

TIER_SCORES = {"critical": 100, "high": 75, "medium": 50, "low": 25}

# Seuils de conversion score -> priorité (P1 = le plus urgent)
PRIORITY_THRESHOLDS = [
    (80, "P1"),
    (60, "P2"),
    (35, "P3"),
    (0, "P4"),
]

# À partir de quel % du délai SLA écoulé on considère le risque de
# dépassement comme imminent (donc à signaler AVANT le dépassement réel)
SLA_WARNING_RATIO = 0.8


@dataclass
class SeverityResult:
    entity: str
    priority: str                  # "P1".."P4"
    severity_score: int            # 0-100
    business_tier: str
    blast_radius: int              # nombre d'entités affectées (dont la racine)
    incident_duration_minutes: float
    sla_target_minutes: int
    sla_elapsed_ratio: float        # 0.0 à 1.0+ (>1 = SLA déjà dépassé)
    sla_breach_risk: bool           # True si on approche ou dépasse le SLA
    sla_minutes_remaining: float    # peut être négatif si déjà dépassé

    def to_dict(self) -> dict:
        return {
            "entity": self.entity,
            "priority": self.priority,
            "severity_score": self.severity_score,
            "business_tier": self.business_tier,
            "blast_radius": self.blast_radius,
            "incident_duration_minutes": round(self.incident_duration_minutes, 1),
            "sla_target_minutes": self.sla_target_minutes,
            "sla_elapsed_ratio": round(self.sla_elapsed_ratio, 2),
            "sla_breach_risk": self.sla_breach_risk,
            "sla_minutes_remaining": round(self.sla_minutes_remaining, 1),
        }


class SeverityClassifier:
    def __init__(self, criticality_path: str = "business_criticality.yaml"):
        with open(criticality_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        self.criticality: dict[str, dict] = data.get("criticality", {})
        self.default: dict = data.get("default", {"tier": "medium", "sla_minutes": 240})

    def _business_info(self, entity: str) -> dict:
        return self.criticality.get(entity, self.default)

    def classify(self, rca_result: RCAResult, now: datetime | None = None) -> SeverityResult:
        now = now or datetime.now(timezone.utc)
        entity = rca_result.root_cause_entity
        info = self._business_info(entity)
        tier = info["tier"]
        sla_target = info["sla_minutes"]

        # Facteur 1 : impact business (critère 2)
        business_score = TIER_SCORES.get(tier, TIER_SCORES["medium"])

        # Facteur 2 : rayon d'impact — combien d'entités sont touchées par
        # cet incident (la racine + tout ce qui est expliqué par propagation)
        blast_radius = len(rca_result.incident_group.entities)
        blast_score = min(blast_radius, 5) * 20  # plafonné à 100 (5+ entités)

        # Facteur 3 : durée de l'incident, normalisée par rapport au SLA cible
        duration_minutes = (now - rca_result.incident_group.start_time).total_seconds() / 60
        elapsed_ratio = duration_minutes / sla_target if sla_target > 0 else 0
        duration_score = min(elapsed_ratio, 1.0) * 100

        severity_score = round(
            WEIGHT_BUSINESS * business_score
            + WEIGHT_BLAST_RADIUS * blast_score
            + WEIGHT_DURATION * duration_score
        )

        priority = next(p for threshold, p in PRIORITY_THRESHOLDS if severity_score >= threshold)

        return SeverityResult(
            entity=entity,
            priority=priority,
            severity_score=severity_score,
            business_tier=tier,
            blast_radius=blast_radius,
            incident_duration_minutes=duration_minutes,
            sla_target_minutes=sla_target,
            sla_elapsed_ratio=elapsed_ratio,
            sla_breach_risk=elapsed_ratio >= SLA_WARNING_RATIO,
            sla_minutes_remaining=sla_target - duration_minutes,
        )


def prioritize(results: list[SeverityResult]) -> list[SeverityResult]:
    """
    Trie plusieurs incidents simultanés du plus urgent au moins urgent
    (critère 4). Departage à score égal : le plus proche du dépassement
    SLA passe devant (il a moins de marge disponible).
    """
    return sorted(results, key=lambda r: (-r.severity_score, r.sla_minutes_remaining))
