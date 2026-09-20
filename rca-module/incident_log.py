"""
Journal consolidé des interventions (US 4.1).

Contrairement à remediation_history.jsonl (une ligne par TENTATIVE
d'action, cf. knowledge_base.py) et approval_audit.jsonl (une ligne par
DÉCISION d'approbation, cf. approval_engine.py), ce fichier contient
UNE ligne par INCIDENT complet — exactement ce que demande le cahier des
charges : "horodatage, service concerné, action entreprise", avec un
résultat final clair (résolu automatiquement / résolu manuellement /
escaladé / échoué / rejeté).
"""

import json
import os
import time
from dataclasses import dataclass, field


@dataclass
class IncidentRecord:
    timestamp: float
    entity: str
    service: str | None
    error_category: str
    priority: str
    action_taken: str | None
    approval_status: str
    outcome: str   # "resolved_auto" | "resolved_manual" | "escalated" | "failed" | "rejected" | "no_response"
    duration_seconds: float | None = None

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "entity": self.entity,
            "service": self.service,
            "error_category": self.error_category,
            "priority": self.priority,
            "action_taken": self.action_taken,
            "approval_status": self.approval_status,
            "outcome": self.outcome,
            "duration_seconds": self.duration_seconds,
        }


class IncidentLogger:
    def __init__(self, path: str = "incident_log.jsonl"):
        self.path = path

    def log(self, record: IncidentRecord) -> None:
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    def read_all(self) -> list[dict]:
        if not os.path.exists(self.path):
            return []
        with open(self.path, "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
