import os
import time
import pytest

from knowledge_base import KnowledgeBase


@pytest.fixture
def kb(tmp_path):
    """Une KB avec le vrai catalogue, mais un historique temporaire et
    isolé par test (pas de pollution entre tests)."""
    history_file = tmp_path / "history.jsonl"
    return KnowledgeBase(
        catalog_path="remediation_catalog.yaml",
        history_path=str(history_file),
    )


class TestCatalogue:
    def test_categorie_connue_retourne_des_candidats(self, kb):
        options = kb.eligible_remediations("CrashLoopBackOff", entity="crash-test")
        action_ids = {o.action_id for o in options}
        assert "restart_pod" in action_ids
        assert "rollback" not in action_ids  # requires_previous_stable_version non fourni -> exclu

    def test_categorie_inconnue_retombe_sur_unknown(self, kb):
        options = kb.eligible_remediations("CategorieBidon", entity="x")
        assert len(options) == 1
        assert options[0].action_id == "escalate_human"

    def test_permission_denied_seulement_escalade_humaine(self, kb):
        options = kb.eligible_remediations("PermissionDenied", entity="x")
        assert len(options) == 1
        assert options[0].action_id == "escalate_human"
        assert options[0].risk_level == "none"


class TestConditions:
    def test_rollback_eligible_si_version_stable_fournie(self, kb):
        options = kb.eligible_remediations(
            "CrashLoopBackOff", entity="crash-test",
            context={"has_previous_stable_version": True},
        )
        action_ids = {o.action_id for o in options}
        assert "rollback" in action_ids

    def test_rate_limiting_15min_bloque_apres_3_tentatives(self, kb):
        for _ in range(3):
            kb.record_attempt(entity="crash-test", error_category="CrashLoopBackOff",
                               action_id="restart_pod", success=False)

        options = kb.eligible_remediations("CrashLoopBackOff", entity="crash-test")
        action_ids = {o.action_id for o in options}
        assert "restart_pod" not in action_ids  # bloqué : 3 tentatives déjà dans les 15 dernières min

    def test_rate_limiting_est_par_entite(self, kb):
        """3 tentatives sur crash-test ne doivent pas bloquer un AUTRE pod."""
        for _ in range(3):
            kb.record_attempt(entity="crash-test", error_category="CrashLoopBackOff",
                               action_id="restart_pod", success=False)

        options = kb.eligible_remediations("CrashLoopBackOff", entity="autre-pod")
        action_ids = {o.action_id for o in options}
        assert "restart_pod" in action_ids

    def test_rate_limiting_expire_hors_de_la_fenetre(self, kb):
        """Des tentatives anciennes (hors fenêtre de 15 min) ne doivent
        plus compter dans le blocage."""
        old_timestamp = time.time() - (20 * 60)  # il y a 20 minutes
        for _ in range(3):
            kb.record_attempt(entity="crash-test", error_category="CrashLoopBackOff",
                               action_id="restart_pod", success=False, timestamp=old_timestamp)

        options = kb.eligible_remediations("CrashLoopBackOff", entity="crash-test")
        action_ids = {o.action_id for o in options}
        assert "restart_pod" in action_ids  # plus dans la fenêtre de 15 min


class TestHistoriqueEfficacite:
    def test_action_jamais_tentee_a_un_taux_none(self, kb):
        rate, count = kb.effectiveness_rate("scale_up_memory")
        assert rate is None
        assert count == 0

    def test_taux_calcule_correctement(self, kb):
        kb.record_attempt(entity="a", error_category="OutOfMemory", action_id="scale_up_memory", success=True)
        kb.record_attempt(entity="b", error_category="OutOfMemory", action_id="scale_up_memory", success=True)
        kb.record_attempt(entity="c", error_category="OutOfMemory", action_id="scale_up_memory", success=False)

        rate, count = kb.effectiveness_rate("scale_up_memory")
        assert count == 3
        assert rate == pytest.approx(2 / 3)

    def test_action_prouvee_efficace_passe_devant_action_non_testee(self, kb):
        """restart_pod a un historique à 100% de réussite -> doit être
        proposé avant scale_up_memory, jamais testée."""
        kb.record_attempt(entity="x", error_category="OutOfMemory", action_id="restart_pod", success=True)
        kb.record_attempt(entity="y", error_category="OutOfMemory", action_id="restart_pod", success=True)

        options = kb.eligible_remediations("OutOfMemory", entity="z")
        assert options[0].action_id == "restart_pod"
        assert options[0].effectiveness_rate == 1.0

    def test_action_peu_fiable_retrogradee(self, kb):
        """Un taux de réussite faible doit passer après une action mieux notée."""
        kb.record_attempt(entity="x", error_category="OutOfMemory", action_id="restart_pod", success=False)
        kb.record_attempt(entity="y", error_category="OutOfMemory", action_id="restart_pod", success=False)
        kb.record_attempt(entity="z", error_category="OutOfMemory", action_id="scale_up_memory", success=True)

        options = kb.eligible_remediations("OutOfMemory", entity="w")
        assert options[0].action_id == "scale_up_memory"
