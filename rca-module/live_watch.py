"""
Mode surveillance continue (Ctrl+C pour arrêter).

Contrairement à live_demo.py (qui traite UN incident et s'arrête), ce
script tourne en boucle indéfiniment : il surveille un déploiement
Kubernetes en continu, et déclenche automatiquement toute la chaîne
(RCA -> sévérité -> décision -> guardrails -> approbation -> exécution ->
vérification) dès qu'un problème est détecté — sans intervention manuelle.

Usage :
    python live_watch.py --entity crash-test --namespace test-devops-agent \
        --deployment crash-test --error-category DeploymentCrash --poll 5
"""

import argparse
import time

from kubernetes import client, config

from live_demo import run_pipeline
from live_status_writer import LiveStatusWriter
from logging_setup import get_logger

logger = get_logger(__name__)

BAD_WAITING_REASONS = {"CrashLoopBackOff", "Error", "ImagePullBackOff", "RunContainerError"}


def _deployment_is_unhealthy(core_api: client.CoreV1Api, namespace: str, deployment_name: str) -> bool:
    """Vérifie l'état réel des pods du déploiement, directement via
    l'API Kubernetes (pas d'appel Prometheus ici, pour rester simple et
    réactif — Epic 1 fait ce travail plus finement pour les métriques)."""
    try:
        pods = core_api.list_namespaced_pod(namespace=namespace, label_selector=f"app={deployment_name}")
    except client.exceptions.ApiException as e:
        logger.warning("Impossible de vérifier l'état du déploiement : %s", e)
        return False

    for pod in pods.items:
        for cs in (pod.status.container_statuses or []):
            waiting = cs.state.waiting
            if waiting and waiting.reason in BAD_WAITING_REASONS:
                return True
    return False


def watch(entity: str, namespace: str, deployment_name: str, error_category: str,
          poll_seconds: int = 5, confidence: int = 90) -> None:
    config.load_kube_config()
    core_api = client.CoreV1Api()
    live = LiveStatusWriter()
    live.watching_idle(entity)
    live.log_event(f"Surveillance démarrée sur {entity} (toutes les {poll_seconds}s)")
    print(f"Surveillance de '{deployment_name}' dans '{namespace}' — Ctrl+C pour arrêter.")

    handling = False
    try:
        while True:
            unhealthy = _deployment_is_unhealthy(core_api, namespace, deployment_name)

            if unhealthy and not handling:
                handling = True
                live.log_event(f"⚠ Anomalie détectée sur {entity} — déclenchement de la chaîne")
                print(f"\n[{time.strftime('%H:%M:%S')}] Anomalie détectée -> lancement de la remédiation...")
                run_pipeline(
                    entity=entity, error_category=error_category, confidence_percent=confidence,
                    namespace=namespace, deployment_name=deployment_name,
                )
                live.log_event("Retour en surveillance")
                handling = False

            elif not unhealthy and not handling:
                live.watching_idle(entity)

            time.sleep(poll_seconds)
    except KeyboardInterrupt:
        live.log_event("Surveillance arrêtée (Ctrl+C)")
        live.stopped()
        print("\nSurveillance arrêtée.")


def main():
    parser = argparse.ArgumentParser(description="Surveillance continue avec remédiation automatique")
    parser.add_argument("--entity", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--deployment", required=True)
    parser.add_argument("--error-category", required=True)
    parser.add_argument("--confidence", type=int, default=90)
    parser.add_argument("--poll", type=int, default=5, help="Intervalle de vérification, en secondes")
    args = parser.parse_args()

    watch(
        entity=args.entity, namespace=args.namespace, deployment_name=args.deployment,
        error_category=args.error_category, poll_seconds=args.poll, confidence=args.confidence,
    )


if __name__ == "__main__":
    main()
