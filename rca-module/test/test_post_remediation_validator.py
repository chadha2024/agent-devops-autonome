from unittest import mock
import pytest

from knowledge_base import KnowledgeBase
from k8s_executor import ActionResult
from post_remediation_validator import PostRemediationValidator


@pytest.fixture
def kb(tmp_path):
    return KnowledgeBase(
        catalog_path="remediation_catalog.yaml",
        history_path=str(tmp_path / "history.jsonl"),
    )


@pytest.fixture
def fake_executor():
    ex = mock.Mock()
    ex.dry_run = False  # simule un vrai exécuteur (pas dry-run), pour tester la revérification réelle
    ex._wait_pod_ready.return_value = True
    ex._wait_deployment_ready.return_value = True
    return ex


@pytest.fixture
def validator(fake_executor, kb):
    return PostRemediationValidator(
        executor=fake_executor, kb=kb,
        stabilization_seconds=1, sleep_fn=lambda s: None,  # pas de vraie attente en test
    )


def make_action_result(action_id="restart_pod", verified_ready=True):
    return ActionResult(action_id=action_id, target="ns/pod", success=True,
                         message="ok", verified_ready=verified_ready)


class TestDryRun:
    def test_dry_run_ne_tente_jamais_un_vrai_appel_api(self, kb):
        """Régression : en dry_run, executor.core est None -> un vrai
        appel _wait_pod_ready planterait. Le validator doit s'appuyer sur
        le dernier statut connu (verified_ready) plutôt que de retenter."""
        dry_run_executor = mock.Mock()
        dry_run_executor.dry_run = True
        dry_run_executor._wait_pod_ready.side_effect = AttributeError("ne doit jamais être appelé")

        validator = PostRemediationValidator(
            executor=dry_run_executor, kb=kb, stabilization_seconds=1, sleep_fn=lambda s: None,
        )
        with mock.patch("post_remediation_validator.capture_metrics", return_value={}):
            result = validator.validate(
                entity="crash-test", instance="x", error_category="NetworkTimeout",
                action_result=make_action_result(verified_ready=True),
                metrics_before={}, namespace="ns", pod_name="pod",
            )

        assert result.healthy is True
        dry_run_executor._wait_pod_ready.assert_not_called()


class TestPasDeRegression:
    def test_metriques_stables_pas_de_regression(self, validator):
        with mock.patch("post_remediation_validator.capture_metrics", return_value={"cpu_percent": 20.0}):
            result = validator.validate(
                entity="crash-test", instance="192.168.49.2:9100",
                error_category="OutOfMemory", action_result=make_action_result(),
                metrics_before={"cpu_percent": 22.0},  # légère baisse -> bien
                namespace="ns", pod_name="pod",
            )
        assert result.regression_detected is False
        assert result.rollback_triggered is False

    def test_enregistre_le_succes_dans_l_historique(self, validator, kb):
        with mock.patch("post_remediation_validator.capture_metrics", return_value={"cpu_percent": 20.0}):
            validator.validate(
                entity="crash-test", instance="x", error_category="OutOfMemory",
                action_result=make_action_result(), metrics_before={"cpu_percent": 20.0},
                namespace="ns", pod_name="pod",
            )
        rate, count = kb.effectiveness_rate("restart_pod")
        assert count == 1
        assert rate == 1.0


class TestRegressionDetectee:
    def test_pod_plus_sain_est_une_regression(self, validator, fake_executor):
        fake_executor._wait_pod_ready.return_value = False  # ne redevient pas prêt
        with mock.patch("post_remediation_validator.capture_metrics", return_value={"cpu_percent": 20.0}):
            result = validator.validate(
                entity="crash-test", instance="x", error_category="OutOfMemory",
                action_result=make_action_result(), metrics_before={"cpu_percent": 20.0},
                namespace="ns", pod_name="pod",
            )
        assert result.healthy is False
        assert result.regression_detected is True
        assert any("sain" in d for d in result.regression_details)

    def test_metrique_qui_empire_est_une_regression(self, validator):
        with mock.patch("post_remediation_validator.capture_metrics", return_value={"cpu_percent": 90.0}):
            result = validator.validate(
                entity="crash-test", instance="x", error_category="OutOfMemory",
                action_result=make_action_result(), metrics_before={"cpu_percent": 20.0},
                namespace="ns", pod_name="pod",
            )
        assert result.regression_detected is True
        assert any("cpu_percent" in d for d in result.regression_details)

    def test_leger_delta_sous_la_marge_n_est_pas_une_regression(self, validator):
        # 20 -> 21 = +5%, sous la marge de 10% tolérée
        with mock.patch("post_remediation_validator.capture_metrics", return_value={"cpu_percent": 21.0}):
            result = validator.validate(
                entity="crash-test", instance="x", error_category="OutOfMemory",
                action_result=make_action_result(), metrics_before={"cpu_percent": 20.0},
                namespace="ns", pod_name="pod",
            )
        assert result.regression_detected is False

    def test_metrique_manquante_ne_declenche_pas_de_faux_positif(self, validator):
        with mock.patch("post_remediation_validator.capture_metrics", return_value={"cpu_percent": None}):
            result = validator.validate(
                entity="crash-test", instance="x", error_category="OutOfMemory",
                action_result=make_action_result(), metrics_before={"cpu_percent": None},
                namespace="ns", pod_name="pod",
            )
        assert result.regression_detected is False

    def test_enregistre_l_echec_dans_l_historique(self, validator, kb):
        with mock.patch("post_remediation_validator.capture_metrics", return_value={"cpu_percent": 90.0}):
            validator.validate(
                entity="crash-test", instance="x", error_category="OutOfMemory",
                action_result=make_action_result(), metrics_before={"cpu_percent": 20.0},
                namespace="ns", pod_name="pod",
            )
        rate, count = kb.effectiveness_rate("restart_pod")
        assert count == 1
        assert rate == 0.0


class TestRollbackAutomatique:
    def test_rollback_declenche_si_disponible(self, validator, fake_executor):
        fake_executor.rollback.return_value = ActionResult(
            action_id="rollback", target="ns/api", success=True, message="ok", verified_ready=True,
        )
        with mock.patch("post_remediation_validator.capture_metrics", return_value={"cpu_percent": 95.0}):
            result = validator.validate(
                entity="api", instance="x", error_category="CrashLoopBackOff",  # a bien un rollback dans le catalogue
                action_result=make_action_result(), metrics_before={"cpu_percent": 20.0},
                namespace="ns", deployment_name="api",
            )
        assert result.rollback_triggered is True
        fake_executor.rollback.assert_called_once_with("ns", "api")

    def test_pas_de_rollback_disponible_pour_cette_categorie(self, validator, fake_executor):
        """OutOfMemory n'a pas de 'rollback' dans le catalogue -> ne doit
        pas essayer d'en faire un quand même."""
        with mock.patch("post_remediation_validator.capture_metrics", return_value={"cpu_percent": 95.0}):
            result = validator.validate(
                entity="x", instance="x", error_category="OutOfMemory",
                action_result=make_action_result(), metrics_before={"cpu_percent": 20.0},
                namespace="ns", deployment_name="api",
            )
        assert result.rollback_triggered is False
        fake_executor.rollback.assert_not_called()

    def test_rollback_impossible_sans_namespace_deployment(self, validator, fake_executor):
        with mock.patch("post_remediation_validator.capture_metrics", return_value={"cpu_percent": 95.0}):
            result = validator.validate(
                entity="api", instance="x", error_category="CrashLoopBackOff",
                action_result=make_action_result(), metrics_before={"cpu_percent": 20.0},
                # pas de namespace/deployment_name fournis
            )
        assert result.rollback_triggered is False
        fake_executor.rollback.assert_not_called()

    def test_resultat_est_json_serialisable(self, validator):
        import json
        with mock.patch("post_remediation_validator.capture_metrics", return_value={"cpu_percent": 20.0}):
            result = validator.validate(
                entity="x", instance="x", error_category="OutOfMemory",
                action_result=make_action_result(), metrics_before={"cpu_percent": 20.0},
                namespace="ns", pod_name="pod",
            )
        json.dumps(result.to_dict())