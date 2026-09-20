"""
Vérification post-remédiation et rollback.

Ferme la boucle : après qu'une action ait été exécutée (k8s_executor.py),
ce module vérifie que la panne est RÉELLEMENT résolue — pas juste
"techniquement exécuté", mais "les métriques sont revenues à la normale"
(US 3.2 du cahier des charges).

Respecte les 3 critères d'acceptation de l'US 3.2 :
  1. Délai configurable après l'exécution (stabilization_seconds)
  2. Réinterroge Prometheus pour vérifier le retour à la normale
     (comparaison à des seuils absolus, pas juste "avant vs après")
  3. Escalade vers l'équipe de garde (Slack/Teams) si le problème persiste

Le rollback automatique est un ajout AU-DELÀ du strict cahier des
charges (issu du ticket Jira étendu "vérification et rollback") : en
plus d'escalader, on tente une remédiation de secours si disponible.
"""

import time
import yaml
from dataclasses import dataclass, field

from metrics_client import capture_metrics
from k8s_executor import K8sExecutor, ActionResult
from knowledge_base import KnowledgeBase
from approval_engine import NotificationChannel, LogNotificationChannel
from logging_setup import get_logger

logger = get_logger(__name__)

DEFAULT_STABILIZATION_SECONDS = 60   # US 3.2, critère 1 : "ex: 60 secondes"


@dataclass
class ValidationResult:
    entity: str
    action_id: str
    healthy: bool                       # readiness confirmée après stabilisation
    metrics_before: dict
    metrics_after: dict
    problem_persists: bool               # US 3.2 : le VRAI critère — pas "revenu à la normale"
    persistence_details: list[str] = field(default_factory=list)
    escalated_to_oncall: bool = False     # US 3.2, critère 3
    rollback_triggered: bool = False       # bonus (ticket étendu)
    rollback_result: ActionResult | None = None

    def to_dict(self) -> dict:
        return {
            "entity": self.entity,
            "action_id": self.action_id,
            "healthy": self.healthy,
            "metrics_before": self.metrics_before,
            "metrics_after": self.metrics_after,
            "problem_persists": self.problem_persists,
            "persistence_details": self.persistence_details,
            "escalated_to_oncall": self.escalated_to_oncall,
            "rollback_triggered": self.rollback_triggered,
            "rollback_result": self.rollback_result.to_dict() if self.rollback_result else None,
        }


class PostRemediationValidator:
    def __init__(self, executor: K8sExecutor, kb: KnowledgeBase,
                 stabilization_seconds: int = DEFAULT_STABILIZATION_SECONDS,
                 normal_thresholds_path: str = "normal_thresholds.yaml",
                 notification_channel: NotificationChannel | None = None,
                 sleep_fn=time.sleep, health_check_timeout: int = 30):
        self.executor = executor
        self.kb = kb
        self.stabilization_seconds = stabilization_seconds
        self.channel = notification_channel or LogNotificationChannel()
        self._sleep = sleep_fn  # injectable pour les tests (pas de vraie attente)
        self.health_check_timeout = health_check_timeout

        with open(normal_thresholds_path, "r", encoding="utf-8") as f:
            self.normal_thresholds = yaml.safe_load(f).get("normal_thresholds", {})

    # ------------------------------------------------------------------
    def capture_before(self, instance: str) -> dict:
        """À appeler AVANT d'exécuter la remédiation."""
        return capture_metrics(instance)

    def validate(self, entity: str, instance: str, error_category: str,
                 action_result: ActionResult, metrics_before: dict,
                 namespace: str | None = None, deployment_name: str | None = None,
                 pod_name: str | None = None, incident_start_time: float | None = None) -> ValidationResult:
        """
        US 3.2 complète :
          1. Attend le délai de stabilisation
          2. Réinterroge Prometheus, compare aux seuils de "normal"
          3. Si le problème persiste, escalade vers l'équipe de garde
        + bonus : tente un rollback automatique si disponible.
        """
        # Critère 1 : délai configurable après l'exécution
        self._sleep(self.stabilization_seconds)

        # Critère 2 : réinterroge Prometheus, vérifie le retour à la normale
        healthy = self._recheck_health(action_result, namespace, pod_name, deployment_name)
        metrics_after = capture_metrics(instance)
        persists, details = self._check_problem_persists(metrics_after, healthy)

        result = ValidationResult(
            entity=entity, action_id=action_result.action_id, healthy=healthy,
            metrics_before=metrics_before, metrics_after=metrics_after,
            problem_persists=persists, persistence_details=details,
        )

        self.kb.record_attempt(
            entity=entity, error_category=error_category,
            action_id=action_result.action_id, success=not persists,
            incident_start_time=incident_start_time,
        )

        if persists:
            logger.warning(
                "PROBLÈME PERSISTANT sur %s après %s : %s",
                entity, action_result.action_id, "; ".join(details),
            )
            # Critère 3 : escalade vers l'équipe de garde (Slack/Teams —
            # LogNotificationChannel pour l'instant, branchable plus tard
            # sans changer cette logique, cf. approval_engine.py)
            self.channel.notify(
                "on-call-team",
                f"⚠️ Panne persistante sur {entity} après remédiation "
                f"({action_result.action_id}) : {'; '.join(details)}. "
                f"Intervention manuelle requise.",
            )
            result.escalated_to_oncall = True

            # Bonus (ticket étendu) : tenter un rollback automatique en plus
            result.rollback_triggered, result.rollback_result = self._attempt_rollback(
                entity, error_category, namespace, deployment_name,
            )

        return result

    # ------------------------------------------------------------------
    def _recheck_health(self, action_result: ActionResult, namespace, pod_name, deployment_name) -> bool:
        """Health check APRÈS stabilisation, pas seulement le readiness
        probe immédiat de k8s_executor (qui peut re-crasher juste après).

        En dry_run, l'executor n'a pas de vrai client Kubernetes (self.core
        est None) — rien n'a vraiment changé sur le cluster, donc on se
        contente du dernier statut connu plutôt que de tenter un vrai
        appel API qui planterait."""
        if self.executor.dry_run:
            return action_result.verified_ready
        if pod_name and namespace:
            return self.executor._wait_pod_ready(namespace, pod_name, timeout=self.health_check_timeout)
        if deployment_name and namespace:
            return self.executor._wait_deployment_ready(namespace, deployment_name, timeout=self.health_check_timeout)
        return action_result.verified_ready  # repli : dernier statut connu

    def _check_problem_persists(self, metrics_after: dict, healthy: bool) -> tuple[bool, list[str]]:
        """
        US 3.2, critère 2 : compare aux seuils absolus de "normal", PAS
        seulement "est-ce moins pire qu'avant". Une métrique encore
        au-dessus de son seuil de normalité veut dire que le problème
        persiste, même si elle s'est légèrement améliorée par rapport à
        avant l'action.
        """
        details = []
        if not healthy:
            details.append("le pod/déploiement n'est pas revenu à un état sain")

        for metric_name, value in metrics_after.items():
            if value is None:
                continue  # donnée manquante -> on ne peut rien conclure sur cette métrique
            normal_max = self.normal_thresholds.get(metric_name)
            if normal_max is not None and value > normal_max:
                details.append(
                    f"{metric_name} toujours anormal : {value:.1f} > seuil normal ({normal_max})"
                )

        return len(details) > 0, details

    def _attempt_rollback(self, entity, error_category, namespace, deployment_name):
        """Bonus au-delà du strict cahier des charges : tente un rollback
        automatique si une option existe et reste éligible dans le
        catalogue (ex: pas déjà tenté 2x cette heure)."""
        candidates = self.kb.eligible_remediations(
            error_category, entity, context={"has_previous_stable_version": True},
        )
        if not any(c.action_id == "rollback" for c in candidates):
            logger.warning("Aucun rollback disponible/éligible pour %s — reste sur l'escalade humaine", entity)
            return False, None

        if not (namespace and deployment_name):
            logger.warning("Rollback impossible sans namespace/deployment_name connus pour %s", entity)
            return False, None

        rollback_result = self.executor.rollback(namespace, deployment_name)
        self.kb.record_attempt(
            entity=entity, error_category=error_category,
            action_id="rollback", success=rollback_result.success,
        )
        return True, rollback_result
