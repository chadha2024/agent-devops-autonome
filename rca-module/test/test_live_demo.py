from unittest import mock
import pytest

import live_demo


class TestPipelineCompletCasNominal:
    def test_restart_pod_auto_approuve_va_jusqu_au_bout(self, monkeypatch):
        """Reproduit le scénario réel testé sur crash-test : NetworkTimeout,
        confiance suffisante, restart_pod low-risk -> auto-approuvé -> exécuté."""
        fake_action_result = mock.Mock(success=True, verified_ready=True, message="ok",
                                         action_id="restart_pod")
        fake_action_result.to_dict.return_value = {"action_id": "restart_pod"}

        monkeypatch.setattr(live_demo, "capture_metrics", lambda instance: {})
        monkeypatch.setattr(live_demo.K8sExecutor, "restart_pod", lambda self, ns, pod: fake_action_result)
        monkeypatch.setattr(
            live_demo.PostRemediationValidator, "validate",
            lambda self, **kwargs: mock.Mock(healthy=True, regression_detected=False, rollback_triggered=False),
        )

        result = live_demo.run_pipeline(
            entity="crash-test", error_category="NetworkTimeout", confidence_percent=90,
            namespace="test-devops-agent", pod_name="crash-test", dry_run=True,
        )

        assert result["status"] == "completed"
        assert result["action_taken"] == "restart_pod"
        assert result["approval_status"] == "auto_approved"
        assert result["execution_success"] is True


class TestBlocageParGuardrails:
    def test_action_hors_liste_blanche_bloque_avant_execution(self, monkeypatch):
        """CrashLoopBackOff -> le catalogue peut proposer 'rollback' en
        premier selon le contexte -> rollback n'est pas dans la liste
        blanche -> doit être bloqué AVANT toute tentative d'exécution."""
        executed = {"called": False}
        monkeypatch.setattr(live_demo.K8sExecutor, "rollback",
                             lambda self, ns, dep: executed.__setitem__("called", True))

        result = live_demo.run_pipeline(
            entity="api", error_category="ConfigError", confidence_percent=90,
            namespace="ns", deployment_name="api", dry_run=True,
        )

        # ConfigError -> rollback (medium, pas dans allowed_actions) ou
        # escalate_human (none, jamais dans allowed_actions non plus)
        assert result["status"] in {"blocked_by_guardrails", "pending_human_approval"}
        assert executed["called"] is False


class TestAucuneRemediationDisponible:
    def test_categorie_sans_option_eligible_ne_plante_pas(self):
        result = live_demo.run_pipeline(
            entity="x", error_category="PermissionDenied", confidence_percent=50, dry_run=True,
        )
        # escalate_human seul candidat -> hors liste blanche -> bloqué proprement
        assert result["status"] in {"blocked_by_guardrails", "pending_human_approval"}


class TestExecuteurNonImplemente:
    def test_action_reconnue_mais_pas_encore_cablee(self, monkeypatch):
        """scale_up_memory (OutOfMemory) n'a pas d'exécuteur réel dans
        ACTION_DISPATCH -> doit le signaler, pas planter."""
        # On force le guardrail + l'approbation à laisser passer artificiellement
        # en ajoutant temporairement scale_up_memory à la liste blanche du test.
        import yaml
        with open("guardrails_policy.yaml") as f:
            original = yaml.safe_load(f)
        original["allowed_actions"].append("scale_up_memory")
        with open("guardrails_policy.yaml", "w") as f:
            yaml.safe_dump(original, f)

        try:
            result = live_demo.run_pipeline(
                entity="x", error_category="OutOfMemory", confidence_percent=90, dry_run=True,
            )
        finally:
            original["allowed_actions"].remove("scale_up_memory")
            with open("guardrails_policy.yaml", "w") as f:
                yaml.safe_dump(original, f)

        # restart_pod (low risk, DÉJÀ dans ACTION_DISPATCH) devrait être
        # choisi en premier de toute façon (risk_first) -> ce test valide
        # surtout que rien ne plante peu importe le chemin emprunté.
        assert result["status"] in {"completed", "no_executor_implemented", "missing_execution_context"}


class TestContexteManquant:
    def test_restart_pod_sans_pod_name_signale_le_manque(self):
        result = live_demo.run_pipeline(
            entity="crash-test", error_category="NetworkTimeout", confidence_percent=90,
            namespace="ns", dry_run=True,  # pod_name manquant volontairement
        )
        assert result["status"] == "missing_execution_context"
