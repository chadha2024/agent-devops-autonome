"""
Démo live — orchestre toute la chaîne OAPE d'un bout à l'autre, sous les
yeux de l'audience : diagnostic -> RCA -> sévérité -> décision ->
guardrails -> approbation -> exécution réelle -> vérification.

Usage (à lancer contre un vrai cluster, ex: le pod crash-test) :
    python live_demo.py --entity crash-test --namespace test-devops-agent \
        --pod crash-test --error-category NetworkTimeout --confidence 90

⚠️ Limite honnête : tous les action_id du catalogue n'ont pas encore
d'exécuteur réel implémenté (ex: scale_up_memory, cleanup_old_logs).
ACTION_DISPATCH ne mappe que ce qui est réellement câblé aujourd'hui ;
le reste est signalé explicitement plutôt que de planter ou de mentir.

US 4.2 : si approval_policy.yaml a manual_approval_mode=true, OU si le
risque de l'action nécessite une validation (medium/high), l'exécution
attend un vrai clic "Approuver"/"Rejeter" dans le dashboard avant de
continuer — elle ne s'arrête plus simplement en "pending".
"""

import argparse
import json
import time
from datetime import datetime, timezone

from alert_model import Alert
from alert_correlator import IncidentGroup
from service_graph import ServiceGraph
from rca_engine import RCAEngine
from severity_engine import SeverityClassifier
from knowledge_base import KnowledgeBase
from decision_engine import DecisionEngine
from guardrails_engine import GuardrailsEngine
from approval_engine import ApprovalManager, ApprovalStatus
from k8s_executor import K8sExecutor
from post_remediation_validator import PostRemediationValidator
from metrics_client import capture_metrics
from live_status_writer import LiveStatusWriter
from human_bridge import wait_for_human_choice
from incident_log import IncidentLogger, IncidentRecord
from logging_setup import get_logger

logger = get_logger(__name__)


def _step(title: str) -> None:
    print(f"\n{'=' * 60}\n{title}\n{'=' * 60}")


# Actions du catalogue réellement câblées à un exécuteur aujourd'hui.
# Toute action absente d'ici est signalée, pas exécutée à l'aveugle.
ACTION_DISPATCH = {
    "restart_pod": lambda ex, ctx: ex.restart_pod(ctx["namespace"], ctx["pod_name"]) if ctx.get("pod_name") else None,
    "restart_deployment": lambda ex, ctx: ex.restart_deployment(ctx["namespace"], ctx["deployment_name"]) if ctx.get("deployment_name") else None,
    "rollback": lambda ex, ctx: ex.rollback(ctx["namespace"], ctx["deployment_name"]) if ctx.get("deployment_name") else None,
    "cordon_node": lambda ex, ctx: ex.cordon_node(ctx["node_name"]) if ctx.get("node_name") else None,
}


def run_pipeline(entity: str, error_category: str, confidence_percent: int,
                  namespace: str | None = None, pod_name: str | None = None,
                  deployment_name: str | None = None, node_name: str | None = None,
                  dry_run: bool = False) -> dict:
    """Exécute la chaîne complète pour UN incident, et retourne un résumé JSON."""

    graph = ServiceGraph(path="service_graph.yaml")
    kb = KnowledgeBase(catalog_path="remediation_catalog.yaml")
    decision_engine = DecisionEngine(kb)
    guardrails = GuardrailsEngine(policy_path="guardrails_policy.yaml")
    approval_manager = ApprovalManager(policy_path="approval_policy.yaml")
    executor = K8sExecutor(dry_run=dry_run)
    incident_logger = IncidentLogger()
    live = LiveStatusWriter()
    live.start(entity)

    start_time = time.time()

    def log_incident(priority, action_taken, approval_status, outcome):
        incident_logger.log(IncidentRecord(
            timestamp=start_time, entity=entity, service=entity,
            error_category=error_category, priority=priority,
            action_taken=action_taken, approval_status=approval_status,
            outcome=outcome, duration_seconds=time.time() - start_time,
        ))

    # 1. Alerte -----------------------------------------------------------
    _step("1. ALERTE REÇUE")
    alert = Alert(
        timestamp=datetime.now(timezone.utc), source="live_demo",
        entity=entity, severity="critical", detail=error_category, raw={},
    )
    print(f"Entité : {alert.entity} | Catégorie : {error_category}")
    live.step("Alerte", "done", f"{entity} — {error_category}")
    live.log_event(f"Alerte reçue sur {entity} ({error_category})")

    # 2. Corrélation + RCA --------------------------------------------------
    _step("2. CORRÉLATION ET CAUSE RACINE (RCA)")
    group = IncidentGroup(alerts=[alert])
    rca_result = RCAEngine(graph).analyze(group)
    print(f"Cause racine : {rca_result.root_cause_entity} ({rca_result.root_cause_type}), "
          f"confiance = {rca_result.confidence_percent}%")
    live.step("RCA", "done", f"{rca_result.root_cause_entity} ({rca_result.confidence_percent}%)")
    live.log_event(f"Cause racine : {rca_result.root_cause_entity} (confiance {rca_result.confidence_percent}%)")

    # 3. Sévérité ------------------------------------------------------------
    _step("3. CLASSIFICATION DE SÉVÉRITÉ")
    severity = SeverityClassifier(criticality_path="business_criticality.yaml").classify(rca_result)
    print(f"Priorité : {severity.priority} | Score : {severity.severity_score}/100 | "
          f"SLA en danger : {severity.sla_breach_risk}")
    live.step("Sévérité", "done", f"Priorité {severity.priority} (score {severity.severity_score}/100)")
    live.log_event(f"Sévérité classée {severity.priority}")

    # 4. Décision --------------------------------------------------------------
    _step("4. MOTEUR DE DÉCISION")
    plan = decision_engine.build_plan(entity=entity, error_category=error_category, priority=severity.priority)
    human_override = None

    if not plan.primary_step:
        print("Aucune remédiation automatique disponible -> le dashboard demande un choix humain.")
        live.step("Décision", "waiting", "aucune option automatique — choix humain requis")
        live.log_event("⚠ Aucune remédiation automatique éligible — choix humain requis")
        candidates = _catalog_candidates(kb, error_category)
        chosen_id = wait_for_human_choice(candidates, live)
        if not chosen_id:
            live.step("Décision", "failed", "aucun choix reçu (timeout)")
            live.finish(False)
            log_incident(severity.priority, None, "no_response", "no_response")
            return {"status": "no_human_response"}
        human_override = _find_candidate(candidates, chosen_id)
        if not human_override:
            live.step("Décision", "failed", f"choix invalide reçu : {chosen_id}")
            live.finish(False)
            log_incident(severity.priority, chosen_id, "invalid_choice", "failed")
            return {"status": "invalid_human_choice"}
        live.step("Décision", "done", f"{chosen_id} (choisi manuellement)")
        live.log_event(f"Action choisie manuellement : {chosen_id}")
    else:
        print(f"Action retenue : {plan.primary_step.action_id} (risque={plan.primary_step.risk_level}, "
              f"stratégie={plan.selection_strategy})")
        print(f"Cascade complète : {[s.action_id for s in plan.steps]}")
        live.step("Décision", "done", f"{plan.primary_step.action_id} (risque {plan.primary_step.risk_level})")
        live.log_event(f"Remédiation choisie : {plan.primary_step.action_id}")

    action_id = human_override["action_id"] if human_override else plan.primary_step.action_id

    # 5. Guardrails --------------------------------------------------------------
    _step("5. GUARDRAILS (dernier filet de sécurité)")
    if human_override:
        live.step("Guardrails", "done", "choix humain — guardrails non applicables")
        live.log_event("Guardrails : contournés (décision humaine explicite)")
    else:
        guard_check = guardrails.check(action_id, risk_level=plan.primary_step.risk_level)
        print(f"Autorisé : {guard_check.allowed}" + (f" | Motifs : {'; '.join(guard_check.reasons)}" if not guard_check.allowed else ""))
        if not guard_check.allowed:
            print("Bloqué par les guardrails -> le dashboard demande un choix humain.")
            live.step("Guardrails", "waiting", "; ".join(guard_check.reasons))
            live.log_event("⚠ Action bloquée par les guardrails — choix humain requis")
            candidates = _catalog_candidates(kb, error_category)
            chosen_id = wait_for_human_choice(candidates, live)
            if not chosen_id:
                live.step("Guardrails", "failed", "aucun choix reçu (timeout)")
                live.finish(False)
                log_incident(severity.priority, action_id, "no_response", "no_response")
                return {"status": "no_human_response"}
            human_override = _find_candidate(candidates, chosen_id)
            if not human_override:
                live.step("Guardrails", "failed", f"choix invalide reçu : {chosen_id}")
                live.finish(False)
                log_incident(severity.priority, chosen_id, "invalid_choice", "failed")
                return {"status": "invalid_human_choice"}
            action_id = human_override["action_id"]
            live.step("Guardrails", "done", f"{chosen_id} (choisi manuellement)")
            live.log_event(f"Action choisie manuellement : {chosen_id}")
        else:
            live.step("Guardrails", "done", "autorisé")
            live.log_event("Guardrails : action autorisée")

    # 6. Approbation -----------------------------------------------------------
    _step("6. APPROBATION")
    approval_status_label = "auto_approved"
    if human_override:
        # L'humain a déjà choisi explicitement -> c'est son approbation.
        live.step("Approbation", "done", "validée par le choix humain")
        live.log_event("Approbation : validée par le choix humain")
        live.set_node_status("remediating")
        approval_status_label = "approved_by_human_choice"
    else:
        approval = approval_manager.request_approval(plan)
        print(f"Statut : {approval.status.value}" + (f" (approbateur : {approval.approver_role})" if approval.status != ApprovalStatus.AUTO_APPROVED else ""))

        if approval.status not in (ApprovalStatus.AUTO_APPROVED, ApprovalStatus.APPROVED):
            # US 4.2 : au lieu de juste s'arrêter, on propose un vrai
            # bouton Approuver/Rejeter dans le dashboard, et on ATTEND.
            print("En attente d'une validation humaine -> boutons Approuver/Rejeter affichés.")
            live.step("Approbation", "waiting", f"en attente de {approval.approver_role}")
            live.log_event(f"⚠ Validation humaine requise ({approval.approver_role})")
            decision_candidates = [
                {"action_id": "approve", "description": f"Approuver : {action_id}", "risk_level": "low"},
                {"action_id": "reject", "description": f"Rejeter : {action_id}", "risk_level": "high"},
            ]
            decision = wait_for_human_choice(decision_candidates, live,
                                              timeout_seconds=approval_manager._timeout_minutes(severity.priority) * 60)
            if decision == "approve":
                approval = approval_manager.decide(approval, approved=True, decided_by="dashboard-user")
                live.step("Approbation", "done", "approuvée manuellement")
                live.log_event("Approbation : approuvée via le dashboard")
                approval_status_label = "approved"
            elif decision == "reject":
                approval_manager.decide(approval, approved=False, decided_by="dashboard-user")
                live.step("Approbation", "failed", "rejetée manuellement")
                live.log_event("Approbation : rejetée via le dashboard")
                live.finish(False)
                log_incident(severity.priority, action_id, "rejected", "rejected")
                return {"status": "rejected_by_human", "approval": approval.to_dict()}
            else:
                live.step("Approbation", "failed", "aucune réponse (timeout)")
                live.log_event("Approbation : timeout, aucune réponse")
                live.finish(False)
                log_incident(severity.priority, action_id, "no_response", "escalated")
                return {"status": "pending_human_approval", "approval": approval.to_dict()}
        else:
            live.step("Approbation", "done", approval.status.value)
            live.log_event(f"Approbation : {approval.status.value}")
            approval_status_label = approval.status.value
        live.set_node_status("remediating")

    # 7. Exécution réelle ----------------------------------------------------------
    _step("7. EXÉCUTION")
    ctx = {"namespace": namespace, "pod_name": pod_name, "deployment_name": deployment_name, "node_name": node_name}
    dispatch = ACTION_DISPATCH.get(action_id)
    metrics_before = capture_metrics(node_name) if node_name else {}

    if dispatch is None:
        print(f"⚠️ Aucun exécuteur réel câblé pour '{action_id}' — "
              f"action non exécutée (à implémenter dans un prochain ticket).")
        live.step("Exécution", "failed", "exécuteur non implémenté")
        live.finish(False)
        log_incident(severity.priority, action_id, approval_status_label, "failed")
        return {"status": "no_executor_implemented", "action_id": action_id}

    action_result = dispatch(executor, ctx)
    if action_result is None:
        print("Informations manquantes (namespace/pod/déploiement/nœud) pour exécuter cette action.")
        live.step("Exécution", "failed", "contexte d'exécution manquant")
        live.finish(False)
        log_incident(severity.priority, action_id, approval_status_label, "failed")
        return {"status": "missing_execution_context"}
    print(f"Résultat : succès={action_result.success}, prêt={action_result.verified_ready}, "
          f"message={action_result.message}")
    live.step("Exécution", "done" if action_result.success else "failed", action_result.message[:120])
    live.log_event(f"Exécution : {action_result.action_id} -> succès={action_result.success}")

    # 8. Vérification post-remédiation ------------------------------------------------
    _step("8. VÉRIFICATION POST-REMÉDIATION")
    live.step("Vérification", "active", "stabilisation en cours...")
    validator = PostRemediationValidator(executor=executor, kb=kb, stabilization_seconds=10)
    validation = validator.validate(
        entity=entity, instance=node_name or "unknown", error_category=error_category,
        action_result=action_result, metrics_before=metrics_before,
        namespace=namespace, pod_name=pod_name, deployment_name=deployment_name,
        incident_start_time=group.start_time.timestamp(),
    )
    print(f"Sain après stabilisation : {validation.healthy} | Problème persistant : {validation.problem_persists}")
    if validation.rollback_triggered:
        print(f"⚠️ Rollback automatique déclenché : {validation.rollback_result.to_dict()}")
    success = validation.healthy and not validation.problem_persists
    live.step("Vérification", "done" if success else "failed",
              "sain, pas de régression" if success else "problème persistant")
    live.log_event("Réparation confirmée ✓" if success else "Échec de la remédiation")
    live.finish(success)

    outcome = "resolved_auto" if (success and approval_status_label == "auto_approved") else (
        "resolved_manual" if success else ("escalated" if validation.escalated_to_oncall else "failed")
    )
    log_incident(severity.priority, action_id, approval_status_label, outcome)

    _step("RÉSUMÉ")
    summary = {
        "status": "completed",
        "entity": entity,
        "root_cause": rca_result.root_cause_entity,
        "priority": severity.priority,
        "action_taken": action_id,
        "chosen_by_human": human_override is not None,
        "approval_status": approval_status_label,
        "execution_success": action_result.success,
        "healthy_after": validation.healthy,
        "problem_persists": validation.problem_persists,
        "escalated_to_oncall": validation.escalated_to_oncall,
        "outcome": outcome,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return summary


def _catalog_candidates(kb: KnowledgeBase, error_category: str) -> list[dict]:
    entries = kb.catalog.get(error_category, kb.catalog.get("Unknown", []))
    return [
        {"action_id": e["action_id"], "description": e["description"], "risk_level": e.get("risk_level", "medium")}
        for e in entries
    ]


def _find_candidate(candidates: list[dict], action_id: str) -> dict | None:
    return next((c for c in candidates if c["action_id"] == action_id), None)


def main():
    parser = argparse.ArgumentParser(description="Démo live de la chaîne complète de l'agent")
    parser.add_argument("--entity", required=True)
    parser.add_argument("--error-category", required=True)
    parser.add_argument("--confidence", type=int, default=90)
    parser.add_argument("--namespace")
    parser.add_argument("--pod")
    parser.add_argument("--deployment")
    parser.add_argument("--node")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    run_pipeline(
        entity=args.entity, error_category=args.error_category, confidence_percent=args.confidence,
        namespace=args.namespace, pod_name=args.pod, deployment_name=args.deployment,
        node_name=args.node, dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
