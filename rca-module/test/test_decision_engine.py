import pytest

from knowledge_base import KnowledgeBase
from decision_engine import DecisionEngine


@pytest.fixture
def kb(tmp_path):
    return KnowledgeBase(
        catalog_path="remediation_catalog.yaml",
        history_path=str(tmp_path / "history.jsonl"),
    )


@pytest.fixture
def engine(kb):
    return DecisionEngine(kb)


class TestSelectionParDefaut:
    def test_priorite_normale_choisit_le_moins_risque_en_premier(self, engine):
        """P3 (pas P1) -> stratégie risk_first : restart_pod (low) doit
        passer avant scale_up_memory (medium), même sans historique."""
        plan = engine.build_plan(entity="x", error_category="OutOfMemory", priority="P3")

        assert plan.selection_strategy == "risk_first"
        assert plan.primary_step.action_id == "restart_pod"
        assert plan.primary_step.risk_level == "low"

    def test_cascade_contient_toutes_les_options_dans_l_ordre(self, engine):
        plan = engine.build_plan(entity="x", error_category="OutOfMemory", priority="P3")
        action_ids = [s.action_id for s in plan.steps]
        assert action_ids == ["restart_pod", "scale_up_memory"]
        assert [s.step_number for s in plan.steps] == [1, 2]


class TestSelectionP1:
    def test_p1_bascule_en_efficacite_d_abord(self, engine):
        """Même sans historique, la stratégie doit changer pour P1."""
        plan = engine.build_plan(entity="x", error_category="OutOfMemory", priority="P1")
        assert plan.selection_strategy == "efficacy_first"

    def test_p1_privilegie_une_action_prouvee_meme_plus_risquee(self, kb, engine):
        """scale_up_memory (medium) prouvée efficace doit passer AVANT
        restart_pod (low) jamais testée, uniquement en P1."""
        kb.record_attempt(entity="y", error_category="OutOfMemory", action_id="scale_up_memory", success=True)
        kb.record_attempt(entity="z", error_category="OutOfMemory", action_id="scale_up_memory", success=True)

        plan_p1 = engine.build_plan(entity="x", error_category="OutOfMemory", priority="P1")
        plan_p3 = engine.build_plan(entity="x", error_category="OutOfMemory", priority="P3")

        assert plan_p1.primary_step.action_id == "scale_up_memory"   # efficacité prime
        assert plan_p3.primary_step.action_id == "restart_pod"        # risque prime toujours en P3


class TestEscaladeHumaineToujoursEnDernier:
    def test_escalate_human_ne_passe_jamais_avant_une_option_automatisable(self, engine):
        """Régression : le risque 'none' (action humaine) ne doit jamais
        être placé avant une option automatisable, même sans historique
        pour départager — sinon l'agent appellerait un humain avant même
        d'essayer l'action automatique la plus sûre."""
        plan = engine.build_plan(entity="crash-test", error_category="NetworkTimeout", priority="P4")
        action_ids = [s.action_id for s in plan.steps]

        assert action_ids == ["restart_pod", "escalate_human"]
        assert plan.primary_step.action_id == "restart_pod"


class TestApprobation:
    def test_action_low_risk_ne_necessite_pas_approbation(self, engine):
        plan = engine.build_plan(entity="x", error_category="OutOfMemory", priority="P3")
        assert plan.primary_step.risk_level == "low"
        assert plan.requires_approval is False

    def test_action_medium_risk_necessite_approbation(self, engine):
        """CrashLoopBackOff sans version stable dispo -> seule option low
        (restart_pod) reste ; on force le cas medium en fournissant le
        contexte qui rend rollback éligible ET en la mettant en premier via P1."""
        kb_context = {"has_previous_stable_version": True}
        # On épuise restart_pod pour qu'il ne reste que rollback (medium)
        engine.kb.record_attempt(entity="x", error_category="CrashLoopBackOff", action_id="restart_pod", success=False)
        engine.kb.record_attempt(entity="x", error_category="CrashLoopBackOff", action_id="restart_pod", success=False)
        engine.kb.record_attempt(entity="x", error_category="CrashLoopBackOff", action_id="restart_pod", success=False)

        plan = engine.build_plan(entity="x", error_category="CrashLoopBackOff", priority="P3", context=kb_context)
        assert plan.primary_step.action_id == "rollback"
        assert plan.primary_step.risk_level == "medium"
        assert plan.requires_approval is True

    def test_escalade_humaine_necessite_toujours_approbation(self, engine):
        plan = engine.build_plan(entity="x", error_category="PermissionDenied", priority="P4")
        assert plan.primary_step.action_id == "escalate_human"
        assert plan.requires_approval is True

    def test_aucune_option_disponible_force_l_approbation(self, engine):
        """Si toutes les remédiations sont bloquées par le rate limiting,
        le plan est vide -> ça doit forcer une intervention humaine plutôt
        que de planter."""
        for _ in range(3):
            engine.kb.record_attempt(entity="x", error_category="NetworkTimeout", action_id="restart_pod", success=False)

        # NetworkTimeout n'a que restart_pod (bloqué) et escalate_human (reste éligible)
        plan = engine.build_plan(entity="x", error_category="NetworkTimeout", priority="P3")
        assert plan.primary_step.action_id == "escalate_human"
        assert plan.requires_approval is True


class TestDowntime:
    def test_worst_case_downtime_est_la_somme_de_toutes_les_etapes(self, engine):
        plan = engine.build_plan(entity="x", error_category="OutOfMemory", priority="P3")
        # restart_pod (15s) + scale_up_memory (90s)
        assert plan.worst_case_downtime_seconds == 105

    def test_to_dict_est_json_serialisable(self, engine):
        import json
        plan = engine.build_plan(entity="x", error_category="DiskFull", priority="P2")
        json.dumps(plan.to_dict())  # ne doit lever aucune exception
