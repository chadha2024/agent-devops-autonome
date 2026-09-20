from unittest import mock
import pytest

from kubernetes.client.rest import ApiException
from k8s_executor import K8sExecutor, POLL_INTERVAL_SECONDS


def make_pod(ready: bool):
    condition = mock.Mock(type="Ready", status="True" if ready else "False")
    pod = mock.Mock()
    pod.status.conditions = [condition]
    return pod


def make_deployment(desired: int, ready: int):
    dep = mock.Mock()
    dep.spec.replicas = desired
    dep.status.ready_replicas = ready
    return dep


@pytest.fixture
def mock_core():
    return mock.Mock()


@pytest.fixture
def mock_apps():
    return mock.Mock()


@pytest.fixture
def executor(mock_core, mock_apps, monkeypatch):
    monkeypatch.setattr("k8s_executor.time.sleep", lambda s: None)  # tests instantanés
    return K8sExecutor(dry_run=False, core_api=mock_core, apps_api=mock_apps)


class TestDryRun:
    def test_dry_run_n_appelle_jamais_l_api(self):
        ex = K8sExecutor(dry_run=True)
        result = ex.restart_pod("ns", "pod-1")
        assert result.success is True
        assert result.verified_ready is True
        assert "dry-run" in result.message


class TestRestartPod:
    def test_succes_pod_redevient_ready(self, executor, mock_core):
        mock_core.read_namespaced_pod.return_value = make_pod(ready=True)
        result = executor.restart_pod("ns", "crash-test")

        mock_core.delete_namespaced_pod.assert_called_once_with(name="crash-test", namespace="ns")
        assert result.success is True
        assert result.verified_ready is True

    def test_suppression_echoue_renvoie_echec_explicite(self, executor, mock_core):
        mock_core.delete_namespaced_pod.side_effect = ApiException(reason="Forbidden")
        result = executor.restart_pod("ns", "crash-test")

        assert result.success is False
        assert "échouée" in result.message

    def test_pod_jamais_recree_verified_false_mais_pas_une_exception(self, executor, mock_core, monkeypatch):
        """Pod isolé sans contrôleur : ne revient jamais -> on le signale
        clairement plutôt que de planter ou mentir sur le résultat."""
        monkeypatch.setattr("k8s_executor.DEFAULT_TIMEOUT_SECONDS", 0)
        mock_core.read_namespaced_pod.side_effect = ApiException(reason="NotFound")
        result = executor.restart_pod("ns", "crash-test", timeout=0)

        assert result.success is True          # la suppression elle-même a marché
        assert result.verified_ready is False   # mais rien n'est revenu
        assert "sans contrôleur" in result.message


class TestScaleHorizontally:
    def test_scale_confirme_quand_replicas_prets(self, executor, mock_apps):
        mock_apps.read_namespaced_deployment.return_value = make_deployment(desired=3, ready=3)
        result = executor.scale_horizontally("ns", "api", replicas=3)

        mock_apps.patch_namespaced_deployment_scale.assert_called_once()
        assert result.verified_ready is True

    def test_scale_pas_encore_pret_dans_le_delai(self, executor, mock_apps):
        mock_apps.read_namespaced_deployment.return_value = make_deployment(desired=3, ready=1)
        result = executor.scale_horizontally("ns", "api", replicas=3, timeout=0)

        assert result.success is True   # l'appel API a réussi
        assert result.verified_ready is False  # mais pas confirmé assez vite


class TestRollback:
    def test_rollback_impossible_sans_historique(self, executor, mock_apps):
        dep = mock.Mock()
        dep.spec.selector.match_labels = {"app": "api"}
        mock_apps.read_namespaced_deployment.return_value = dep

        rs_list = mock.Mock()
        rs_list.items = [mock.Mock(metadata=mock.Mock(annotations={"deployment.kubernetes.io/revision": "1"}))]
        mock_apps.list_namespaced_replica_set.return_value = rs_list

        result = executor.rollback("ns", "api")

        assert result.success is False
        assert "aucune révision précédente" in result.message
        mock_apps.patch_namespaced_deployment.assert_not_called()

    def test_rollback_reussi_avec_2_revisions(self, executor, mock_apps):
        dep = mock.Mock()
        dep.spec.selector.match_labels = {"app": "api"}

        rs_v2 = mock.Mock(metadata=mock.Mock(annotations={"deployment.kubernetes.io/revision": "2"}))
        rs_v1 = mock.Mock(metadata=mock.Mock(annotations={"deployment.kubernetes.io/revision": "1"}))
        rs_v1.spec.template.to_dict.return_value = {"spec": "ancien_template"}
        rs_list = mock.Mock()
        rs_list.items = [rs_v2, rs_v1]
        mock_apps.list_namespaced_replica_set.return_value = rs_list

        # 1er appel (pour le selector) renvoie dep ; 2e appel (vérification
        # post-rollback) renvoie un déploiement prêt.
        mock_apps.read_namespaced_deployment.side_effect = [dep, make_deployment(desired=1, ready=1)]

        result = executor.rollback("ns", "api")

        assert result.success is True
        mock_apps.patch_namespaced_deployment.assert_called_once()


class TestCordonNode:
    def test_cordon_confirme(self, executor, mock_core):
        node = mock.Mock()
        node.spec.unschedulable = True
        mock_core.read_node.return_value = node

        result = executor.cordon_node("minikube")

        mock_core.patch_node.assert_called_once_with(
            name="minikube", body={"spec": {"unschedulable": True}}
        )
        assert result.verified_ready is True

    def test_uncordon_confirme(self, executor, mock_core):
        node = mock.Mock()
        node.spec.unschedulable = False
        mock_core.read_node.return_value = node

        result = executor.uncordon_node("minikube")
        assert result.verified_ready is True


class TestDrainNode:
    def test_drain_evince_seulement_les_pods_non_daemonset(self, executor, mock_core):
        node = mock.Mock()
        node.spec.unschedulable = True
        mock_core.read_node.return_value = node

        pod_normal = mock.Mock()
        pod_normal.metadata.name = "app-1"
        pod_normal.metadata.namespace = "default"
        pod_normal.metadata.owner_references = [mock.Mock(kind="ReplicaSet")]

        pod_daemonset = mock.Mock()
        pod_daemonset.metadata.name = "node-exporter-xyz"
        pod_daemonset.metadata.namespace = "monitoring"
        pod_daemonset.metadata.owner_references = [mock.Mock(kind="DaemonSet")]

        pods = mock.Mock()
        pods.items = [pod_normal, pod_daemonset]
        mock_core.list_pod_for_all_namespaces.return_value = pods

        result = executor.drain_node("minikube")

        assert mock_core.create_namespaced_pod_eviction.call_count == 1  # pas le daemonset
        assert result.success is True

    def test_drain_echoue_si_cordon_prealable_echoue(self, executor, mock_core):
        mock_core.patch_node.side_effect = ApiException(reason="Forbidden")
        result = executor.drain_node("minikube")

        assert result.success is False
        assert "cordon préalable" in result.message
        mock_core.list_pod_for_all_namespaces.assert_not_called()
