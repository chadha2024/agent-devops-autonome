"""
Politiques d'approbation (Ticket "Configurer les politiques d'approbation").

Décide, pour un plan de remédiation donné (decision_engine.py) :
  - s'il s'exécute automatiquement (risque faible, déjà géré en amont)
  - qui doit valider s'il faut un humain, et comment on le notifie
  - ce qui se passe si personne ne répond à temps (escalade, puis alerte
    critique si même l'escalade ne répond pas)

Le canal de notification est volontairement abstrait (interface +
implémentation "log" par défaut) : brancher un vrai Slack/email plus tard
ne demandera pas de toucher à la logique d'approbation elle-même.
"""

import json
import time
from dataclasses import dataclass, field
from enum import Enum

import yaml

from decision_engine import ExecutionPlan
from logging_setup import get_logger

logger = get_logger(__name__)


class ApprovalStatus(str, Enum):
    AUTO_APPROVED = "auto_approved"
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"
    ESCALATION_EXHAUSTED = "escalation_exhausted"  # personne n'a répondu, même après escalade


# ----------------------------------------------------------------------
# Canal de notification — abstrait pour pouvoir brancher Slack/email plus
# tard sans changer la logique d'approbation.
# ----------------------------------------------------------------------
class NotificationChannel:
    def notify(self, role: str, message: str) -> None:
        raise NotImplementedError


class LogNotificationChannel(NotificationChannel):
    """Implémentation par défaut : journalise la notification au lieu de
    l'envoyer réellement. À remplacer par un vrai canal (Slack, email...)
    quand l'intégration existera, sans toucher au reste du module."""

    def notify(self, role: str, message: str) -> None:
        logger.warning("[NOTIFICATION -> %s] %s", role, message)


@dataclass
class ApprovalRequest:
    entity: str
    action_id: str
    risk_level: str
    priority: str
    approver_role: str
    status: ApprovalStatus
    requested_at: float
    timeout_at: float
    escalation_level: int = 0     # 0 = premier approbateur, 1 = escaladé
    decided_by: str | None = None
    decided_at: float | None = None

    def to_dict(self) -> dict:
        return {
            "entity": self.entity,
            "action_id": self.action_id,
            "risk_level": self.risk_level,
            "priority": self.priority,
            "approver_role": self.approver_role,
            "status": self.status.value,
            "requested_at": self.requested_at,
            "timeout_at": self.timeout_at,
            "escalation_level": self.escalation_level,
            "decided_by": self.decided_by,
            "decided_at": self.decided_at,
        }


class ApprovalManager:
    def __init__(self, policy_path: str = "approval_policy.yaml",
                 notification_channel: NotificationChannel | None = None,
                 audit_log_path: str = "approval_audit.jsonl"):
        self.policy_path = policy_path
        with open(policy_path, "r", encoding="utf-8") as f:
            self.policy = yaml.safe_load(f)
        self.channel = notification_channel or LogNotificationChannel()
        self.audit_log_path = audit_log_path

    def _timeout_minutes(self, priority: str) -> int:
        table = self.policy["timeout_minutes_by_priority"]
        return table.get(priority, table.get("P4", 240))

    def _escalation_timeout_minutes(self, priority: str) -> int:
        table = self.policy["escalation"]["escalation_timeout_minutes_by_priority"]
        return table.get(priority, table.get("P4", 120))

    def is_manual_approval_mode(self) -> bool:
        """US 4.2, critère 1 : un seul paramètre global qui force TOUTE
        action à attendre une validation humaine, même celles normalement
        auto-approuvées (risque faible)."""
        self._load_policy()
        return bool(self.policy.get("manual_approval_mode", False))

    def _load_policy(self) -> None:
        with open(self.policy_path, "r", encoding="utf-8") as f:
            self.policy = yaml.safe_load(f)

    def request_approval(self, plan: ExecutionPlan, now: float | None = None) -> ApprovalRequest:
        """
        Crée une demande d'approbation pour l'action primaire du plan.
        Risque faible -> auto-approuvé immédiatement, aucune notification
        (SAUF si manual_approval_mode est activé globalement, cf. US 4.2).
        Sinon -> notifie l'approbateur du bon niveau et démarre le chrono
        de timeout, calculé selon la priorité de l'incident (critère
        "notification + attente d'approbation").
        """
        now = now if now is not None else time.time()
        primary = plan.primary_step
        force_manual = self.is_manual_approval_mode()

        if primary is None or (primary.risk_level == "low" and not force_manual):
            request = ApprovalRequest(
                entity=plan.entity, action_id=primary.action_id if primary else "none",
                risk_level=primary.risk_level if primary else "low",
                priority=plan.priority, approver_role="none",
                status=ApprovalStatus.AUTO_APPROVED,
                requested_at=now, timeout_at=now,
            )
            self._log_audit(request)
            return request

        approver_role = self.policy["approval_chain"].get(
            primary.risk_level, self.policy["approval_chain"]["high"]
        )["approver_role"]
        timeout_at = now + self._timeout_minutes(plan.priority) * 60

        request = ApprovalRequest(
            entity=plan.entity, action_id=primary.action_id, risk_level=primary.risk_level,
            priority=plan.priority, approver_role=approver_role,
            status=ApprovalStatus.PENDING, requested_at=now, timeout_at=timeout_at,
        )
        self.channel.notify(
            approver_role,
            f"Approbation requise : {primary.action_id} sur {plan.entity} "
            f"(risque={primary.risk_level}, priorité={plan.priority}). "
            f"Timeout dans {self._timeout_minutes(plan.priority)} min.",
        )
        return request

    def check_timeout(self, request: ApprovalRequest, now: float | None = None) -> ApprovalRequest:
        """
        Vérifie si une demande en attente a dépassé son délai. Si oui :
          - première fois -> escalade vers le rôle supérieur, nouveau
            chrono plus court (critère "escalation si timeout")
          - déjà escaladée et re-timeout -> plus personne à escalader,
            alerte critique explicite plutôt qu'un blocage silencieux
        """
        now = now if now is not None else time.time()
        if request.status != ApprovalStatus.PENDING or now < request.timeout_at:
            return request

        if request.escalation_level == 0:
            escalate_role = self.policy["escalation"]["escalate_to_role"]
            request.status = ApprovalStatus.ESCALATED
            request.escalation_level = 1
            request.approver_role = escalate_role
            request.timeout_at = now + self._escalation_timeout_minutes(request.priority) * 60
            self.channel.notify(
                escalate_role,
                f"ESCALADE : {request.action_id} sur {request.entity} sans réponse "
                f"(risque={request.risk_level}, priorité={request.priority}).",
            )
            request.status = ApprovalStatus.PENDING  # de nouveau en attente, au niveau escaladé
        else:
            request.status = ApprovalStatus.ESCALATION_EXHAUSTED
            self.channel.notify(
                "critical-alert",
                f"AUCUNE RÉPONSE même après escalade : {request.action_id} sur "
                f"{request.entity} — intervention manuelle urgente requise.",
            )
            self._log_audit(request)

        return request

    def decide(self, request: ApprovalRequest, approved: bool, decided_by: str,
               now: float | None = None) -> ApprovalRequest:
        """Enregistre la décision humaine (approbation ou rejet)."""
        now = now if now is not None else time.time()
        request.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        request.decided_by = decided_by
        request.decided_at = now
        self._log_audit(request)
        return request

    def _log_audit(self, request: ApprovalRequest) -> None:
        with open(self.audit_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(request.to_dict()) + "\n")
