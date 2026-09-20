"""
Base de connaissances des remédiations (Ticket "Knowledge Base").

Trois responsabilités, alignées sur les 3 critères du ticket :
  1. Catalogue : quelles remédiations existent pour quelle catégorie
     d'erreur (remediation_catalog.yaml)
  2. Conditions d'application : filtre les remédiations non éligibles
     pour CET incident précis (rate limiting, prérequis...)
  3. Historique d'efficacité : garde une trace de chaque tentative passée,
     pour calculer un taux de réussite réel par action
"""

import json
import os
import time
from dataclasses import dataclass, field

import yaml

from logging_setup import get_logger

logger = get_logger(__name__)

RISK_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}


@dataclass
class RemediationOption:
    action_id: str
    description: str
    conditions: dict
    risk_level: str
    downtime_seconds: float = 0
    effectiveness_rate: float | None = None  # None = jamais tentée, pas assez de données
    attempts_count: int = 0

    def to_dict(self) -> dict:
        return {
            "action_id": self.action_id,
            "description": self.description,
            "risk_level": self.risk_level,
            "downtime_seconds": self.downtime_seconds,
            "effectiveness_rate": self.effectiveness_rate,
            "attempts_count": self.attempts_count,
        }


class KnowledgeBase:
    def __init__(self, catalog_path: str = "remediation_catalog.yaml",
                 history_path: str = "remediation_history.jsonl"):
        with open(catalog_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        self.catalog: dict[str, list[dict]] = data.get("remediations", {})
        self.history_path = history_path

    # ------------------------------------------------------------------
    # Critère 3 : historique d'efficacité
    # ------------------------------------------------------------------
    def record_attempt(self, entity: str, error_category: str, action_id: str,
                        success: bool, timestamp: float | None = None,
                        incident_start_time: float | None = None) -> None:
        """
        Enregistre une tentative de remédiation (réelle ou simulée pour
        l'instant, en attendant que l'exécution automatique existe).

        `incident_start_time` (optionnel) : timestamp du début de
        l'incident (ex: IncidentGroup.start_time du module RCA). Permet
        de calculer un temps de résolution réel (durée = timestamp de
        cette tentative - début de l'incident), utilisé par
        reporting_engine.py pour le MTTR (Ticket "Audit trail et
        reporting"). Sans cette info, le MTTR ne peut pas être calculé
        pour cette entrée -- pas une erreur, juste une donnée en moins.
        """
        ts = timestamp or time.time()
        entry = {
            "timestamp": ts,
            "entity": entity,
            "error_category": error_category,
            "action_id": action_id,
            "success": success,
            "duration_seconds": (ts - incident_start_time) if incident_start_time else None,
        }
        with open(self.history_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

        if not success:
            # Critère "alertes sur actions échouées" : on alerte tout de
            # suite, à la source, plutôt que d'attendre qu'un rapport
            # périodique le remarque plus tard.
            logger.warning(
                "ÉCHEC DE REMÉDIATION : action='%s' entité='%s' catégorie='%s'",
                action_id, entity, error_category,
            )

    def _read_history(self) -> list[dict]:
        if not os.path.exists(self.history_path):
            return []
        with open(self.history_path, "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def effectiveness_rate(self, action_id: str) -> tuple[float | None, int]:
        """Taux de réussite global de cette action, tous incidents confondus.
        Retourne (None, 0) si jamais tentée — pas assez de données pour juger."""
        attempts = [h for h in self._read_history() if h["action_id"] == action_id]
        if not attempts:
            return None, 0
        successes = sum(1 for a in attempts if a["success"])
        return successes / len(attempts), len(attempts)

    def _recent_attempts_count(self, entity: str, action_id: str, window_seconds: float) -> int:
        cutoff = time.time() - window_seconds
        return sum(
            1 for h in self._read_history()
            if h["entity"] == entity and h["action_id"] == action_id and h["timestamp"] >= cutoff
        )

    # ------------------------------------------------------------------
    # Critère 1 + 2 : catalogue et conditions d'application
    # ------------------------------------------------------------------
    def _is_eligible(self, entity: str, conditions: dict, context: dict) -> bool:
        """Vérifie chaque condition déclarée dans le catalogue. Une
        condition non reconnue est ignorée plutôt que de bloquer
        silencieusement une remédiation légitime."""
        if "max_attempts_per_15min" in conditions:
            if self._recent_attempts_count(entity, context["_action_id"], 15 * 60) >= conditions["max_attempts_per_15min"]:
                return False
        if "max_attempts_per_hour" in conditions:
            if self._recent_attempts_count(entity, context["_action_id"], 60 * 60) >= conditions["max_attempts_per_hour"]:
                return False
        if "max_attempts_per_day" in conditions:
            if self._recent_attempts_count(entity, context["_action_id"], 24 * 60 * 60) >= conditions["max_attempts_per_day"]:
                return False
        if conditions.get("requires_previous_stable_version"):
            if not context.get("has_previous_stable_version", False):
                return False
        return True

    def eligible_remediations(self, error_category: str, entity: str,
                               context: dict | None = None) -> list[RemediationOption]:
        """
        Retourne les remédiations candidates pour cette catégorie d'erreur,
        filtrées par leurs conditions, enrichies de leur taux d'efficacité
        historique, triées des plus fiables/moins risquées aux moins connues.
        """
        context = dict(context or {})
        candidates = self.catalog.get(error_category, self.catalog.get("Unknown", []))

        options = []
        for c in candidates:
            context["_action_id"] = c["action_id"]
            if not self._is_eligible(entity, c.get("conditions", {}), context):
                continue
            rate, count = self.effectiveness_rate(c["action_id"])
            options.append(RemediationOption(
                action_id=c["action_id"],
                description=c["description"],
                conditions=c.get("conditions", {}),
                risk_level=c.get("risk_level", "medium"),
                downtime_seconds=c.get("downtime_seconds", 0),
                effectiveness_rate=rate,
                attempts_count=count,
            ))

        # Tri : d'abord celles avec un historique connu (rate décroissant),
        # puis celles jamais testées (à essayer si rien de prouvé n'existe),
        # départagé par risque croissant (le moins risqué en premier).
        def sort_key(opt: RemediationOption):
            has_history = opt.effectiveness_rate is not None
            return (
                0 if has_history else 1,
                -(opt.effectiveness_rate or 0),
                RISK_ORDER.get(opt.risk_level, 2),
            )

        return sorted(options, key=sort_key)
