"""
Executors de remédiation Kubernetes (Ticket "Actions de remédiation K8s").

Exécute réellement les actions sur le cluster (via le client Python
officiel `kubernetes`, pas des appels `kubectl` en sous-processus, plus
robuste pour parser les statuts). Chaque action a sa propre vérification
immédiate ("readiness probe") : est-ce que le geste technique a
visiblement réussi, indépendamment de la vérification plus poussée
(métriques avant/après) du ticket suivant.

Un flag `dry_run` reste disponible par sécurité (défaut: False, exécution
réelle — validé avec l'encadrant vu l'environnement de test minikube
isolé), pour pouvoir revalider la logique sans toucher au cluster si besoin.
"""

import time
from dataclasses import dataclass, field

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from logging_setup import get_logger

logger = get_logger(__name__)

DEFAULT_TIMEOUT_SECONDS = 60
POLL_INTERVAL_SECONDS = 2


@dataclass
class ActionResult:
    action_id: str
    target: str
    success: bool
    message: str
    verified_ready: bool = False
    duration_seconds: float = 0.0

    def to_dict(self) -> dict:
        return {
            "action_id": self.action_id,
            "target": self.target,
            "success": self.success,
            "message": self.message,
            "verified_ready": self.verified_ready,
            "duration_seconds": round(self.duration_seconds, 2),
        }


class K8sExecutor:
    def __init__(self, dry_run: bool = False, kube_context: str | None = None,
                 core_api=None, apps_api=None):
        """
        `core_api`/`apps_api` : injectables pour les tests (mock), sinon
        chargés depuis le vrai kubeconfig de la machine.
        """
        self.dry_run = dry_run
        if dry_run:
            self.core, self.apps = None, None
        elif core_api is not None or apps_api is not None:
            self.core, self.apps = core_api, apps_api
        else:
            config.load_kube_config(context=kube_context)
            self.core = client.CoreV1Api()
            self.apps = client.AppsV1Api()

    # ------------------------------------------------------------------
    def restart_pod(self, namespace: str, pod_name: str,
                     timeout: int = DEFAULT_TIMEOUT_SECONDS) -> ActionResult:
        target = f"{namespace}/{pod_name}"
        start = time.time()

        if self.dry_run:
            logger.warning("[DRY-RUN] restart_pod %s", target)
            return ActionResult("restart_pod", target, True, "dry-run", verified_ready=True)

        try:
            self.core.delete_namespaced_pod(name=pod_name, namespace=namespace)
        except ApiException as e:
            return ActionResult("restart_pod", target, False, f"suppression échouée: {e.reason}")

        ready = self._wait_pod_ready(namespace, pod_name, timeout)
        message = "pod prêt après redémarrage" if ready else (
            "pas de nouveau pod détecté sous ce nom dans le délai — "
            "probablement un pod isolé sans contrôleur (Deployment/StatefulSet) pour le recréer"
        )
        return ActionResult("restart_pod", target, True, message,
                             verified_ready=ready, duration_seconds=time.time() - start)

    def restart_deployment(self, namespace: str, deployment_name: str,
                            timeout: int = DEFAULT_TIMEOUT_SECONDS) -> ActionResult:
        target = f"{namespace}/{deployment_name}"
        start = time.time()

        if self.dry_run:
            logger.warning("[DRY-RUN] restart_deployment %s", target)
            return ActionResult("restart_deployment", target, True, "dry-run", verified_ready=True)

        patch = {
            "spec": {"template": {"metadata": {"annotations": {
                "devops-agent/restartedAt": str(time.time())
            }}}}
        }
        try:
            self.apps.patch_namespaced_deployment(name=deployment_name, namespace=namespace, body=patch)
        except ApiException as e:
            return ActionResult("restart_deployment", target, False, f"patch échoué: {e.reason}")

        ready = self._wait_deployment_ready(namespace, deployment_name, timeout)
        return ActionResult("restart_deployment", target, True,
                             "rollout terminé" if ready else "rollout pas terminé dans le délai",
                             verified_ready=ready, duration_seconds=time.time() - start)

    def scale_horizontally(self, namespace: str, deployment_name: str, replicas: int,
                            timeout: int = DEFAULT_TIMEOUT_SECONDS) -> ActionResult:
        target = f"{namespace}/{deployment_name}"
        start = time.time()

        if self.dry_run:
            logger.warning("[DRY-RUN] scale_horizontally %s -> %d replicas", target, replicas)
            return ActionResult("scale_horizontally", target, True, "dry-run", verified_ready=True)

        try:
            self.apps.patch_namespaced_deployment_scale(
                name=deployment_name, namespace=namespace, body={"spec": {"replicas": replicas}}
            )
        except ApiException as e:
            return ActionResult("scale_horizontally", target, False, f"scale échoué: {e.reason}")

        ready = self._wait_deployment_ready(namespace, deployment_name, timeout)
        return ActionResult("scale_horizontally", target, True,
                             f"scale à {replicas} réplicas confirmé" if ready else "réplicas pas tous prêts dans le délai",
                             verified_ready=ready, duration_seconds=time.time() - start)

    def rollback(self, namespace: str, deployment_name: str,
                 timeout: int = DEFAULT_TIMEOUT_SECONDS) -> ActionResult:
        """
        Revient à la révision précédente du déploiement, en reprenant le
        template de pod du ReplicaSet juste avant l'actuel. Si aucune
        révision antérieure n'existe, échoue explicitement plutôt que de
        faire n'importe quoi — cohérent avec la condition
        `requires_previous_stable_version` du catalogue de remédiations.
        """
        target = f"{namespace}/{deployment_name}"
        start = time.time()

        if self.dry_run:
            logger.warning("[DRY-RUN] rollback %s", target)
            return ActionResult("rollback", target, True, "dry-run", verified_ready=True)

        try:
            rs_list = self.apps.list_namespaced_replica_set(
                namespace=namespace,
                label_selector=self._deployment_selector(namespace, deployment_name),
            )
        except ApiException as e:
            return ActionResult("rollback", target, False, f"lecture historique échouée: {e.reason}")

        revisions = sorted(
            rs_list.items,
            key=lambda rs: int(rs.metadata.annotations.get("deployment.kubernetes.io/revision", 0)),
            reverse=True,
        )
        if len(revisions) < 2:
            return ActionResult("rollback", target, False,
                                 "aucune révision précédente disponible, rollback impossible")

        previous_template = revisions[1].spec.template
        try:
            self.apps.patch_namespaced_deployment(
                name=deployment_name, namespace=namespace,
                body={"spec": {"template": previous_template.to_dict()}},
            )
        except ApiException as e:
            return ActionResult("rollback", target, False, f"patch rollback échoué: {e.reason}")

        ready = self._wait_deployment_ready(namespace, deployment_name, timeout)
        return ActionResult("rollback", target, True,
                             "rollback terminé" if ready else "rollback pas confirmé dans le délai",
                             verified_ready=ready, duration_seconds=time.time() - start)

    def cordon_node(self, node_name: str) -> ActionResult:
        start = time.time()
        if self.dry_run:
            logger.warning("[DRY-RUN] cordon_node %s", node_name)
            return ActionResult("cordon_node", node_name, True, "dry-run", verified_ready=True)

        try:
            self.core.patch_node(name=node_name, body={"spec": {"unschedulable": True}})
        except ApiException as e:
            return ActionResult("cordon_node", node_name, False, f"cordon échoué: {e.reason}")

        node = self.core.read_node(name=node_name)
        verified = bool(node.spec.unschedulable)
        return ActionResult("cordon_node", node_name, True,
                             "nœud mis en quarantaine (unschedulable)",
                             verified_ready=verified, duration_seconds=time.time() - start)

    def uncordon_node(self, node_name: str) -> ActionResult:
        """Non demandé explicitement par le ticket, mais nécessaire pour
        pouvoir tester cordon_node sans bloquer durablement minikube."""
        start = time.time()
        if self.dry_run:
            return ActionResult("uncordon_node", node_name, True, "dry-run", verified_ready=True)
        try:
            self.core.patch_node(name=node_name, body={"spec": {"unschedulable": False}})
        except ApiException as e:
            return ActionResult("uncordon_node", node_name, False, f"uncordon échoué: {e.reason}")
        node = self.core.read_node(name=node_name)
        return ActionResult("uncordon_node", node_name, True, "nœud réactivé",
                             verified_ready=not node.spec.unschedulable,
                             duration_seconds=time.time() - start)

    def drain_node(self, node_name: str, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> ActionResult:
        """
        Cordon + éviction de tous les pods non-DaemonSet du nœud.
        ⚠️ Sur un cluster à un seul nœud (ex: minikube), ceci bloque
        TOUT le cluster — à ne tester en réel qu'avec une extrême prudence,
        jamais sur un environnement à nœud unique sans y être préparé.
        """
        target = node_name
        start = time.time()
        if self.dry_run:
            logger.warning("[DRY-RUN] drain_node %s", target)
            return ActionResult("drain_node", target, True, "dry-run", verified_ready=True)

        cordon_result = self.cordon_node(node_name)
        if not cordon_result.success:
            return ActionResult("drain_node", target, False, f"cordon préalable échoué: {cordon_result.message}")

        try:
            pods = self.core.list_pod_for_all_namespaces(
                field_selector=f"spec.nodeName={node_name}"
            )
        except ApiException as e:
            return ActionResult("drain_node", target, False, f"listing pods échoué: {e.reason}")

        evicted, failed = 0, 0
        for pod in pods.items:
            is_daemonset = any(o.kind == "DaemonSet" for o in (pod.metadata.owner_references or []))
            if is_daemonset:
                continue
            try:
                eviction = client.V1Eviction(
                    metadata=client.V1ObjectMeta(name=pod.metadata.name, namespace=pod.metadata.namespace)
                )
                self.core.create_namespaced_pod_eviction(
                    name=pod.metadata.name, namespace=pod.metadata.namespace, body=eviction
                )
                evicted += 1
            except ApiException:
                failed += 1

        success = failed == 0
        return ActionResult("drain_node", target, success,
                             f"{evicted} pod(s) évincé(s), {failed} échec(s)",
                             verified_ready=success, duration_seconds=time.time() - start)

    # ------------------------------------------------------------------
    # Vérification post-action ("readiness probes")
    # ------------------------------------------------------------------
    def _wait_pod_ready(self, namespace: str, pod_name: str, timeout: int) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                pod = self.core.read_namespaced_pod(name=pod_name, namespace=namespace)
                conditions = pod.status.conditions or []
                if any(c.type == "Ready" and c.status == "True" for c in conditions):
                    return True
            except ApiException:
                pass  # pod pas encore recréé, ou disparu -> on continue de patienter
            time.sleep(POLL_INTERVAL_SECONDS)
        return False

    def _wait_deployment_ready(self, namespace: str, deployment_name: str, timeout: int) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                dep = self.apps.read_namespaced_deployment(name=deployment_name, namespace=namespace)
                desired = dep.spec.replicas or 0
                ready = dep.status.ready_replicas or 0
                if ready == desired and desired > 0:
                    return True
            except ApiException:
                pass
            time.sleep(POLL_INTERVAL_SECONDS)
        return False

    def _deployment_selector(self, namespace: str, deployment_name: str) -> str:
        dep = self.apps.read_namespaced_deployment(name=deployment_name, namespace=namespace)
        labels = dep.spec.selector.match_labels or {}
        return ",".join(f"{k}={v}" for k, v in labels.items())
