"""
Module de détection d'anomalies — point d'entrée principal.

Boucle en continu :
  1. Interroge Prometheus pour récupérer l'historique de chaque métrique,
     POUR CHAQUE instance/pod (US 1.1)
  2. Applique la détection statistique (z-score), la dégradation progressive,
     ET le seuil critique absolu soutenu dans le temps (US 1.2)
  3. Corrèle les métriques entre elles PAR INSTANCE pour distinguer un vrai
     incident du bruit
  4. Déduplique pour ne pas spammer la même alerte
  5. Émet un événement JSON structuré par incident (US 1.2, critères 2 et 3)

Lancer avec :
    python main.py
"""

import json
import sys
import time

import config
from prometheus_client import query_range, extract_series
from detectors import is_statistical_anomaly, detect_progressive_degradation
from correlation import MetricSignal, correlate_by_instance
from dedup import Deduplicator
from threshold_tracker import ThresholdTracker
from logging_setup import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Requêtes PromQL pour chaque métrique surveillée
# `by (instance)` est essentiel : sans lui, Prometheus agrège tout en une
# seule série et on perd la granularité par pod/nœud (US 1.2, critère 3).
# ---------------------------------------------------------------------------
METRIC_QUERIES = {
    "cpu_percent": (
        '100 - (avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[2m])) * 100)'
    ),
    "ram_percent": (
        "100 * (1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes))"
    ),
    "network_bytes_per_sec": (
        'sum by (instance) (rate(node_network_receive_bytes_total{device!="lo"}[2m]))'
    ),
}


def check_metric(name: str, promql: str) -> list[MetricSignal]:
    """
    Récupère l'historique d'une métrique pour TOUTES les instances/pods
    concernés, et applique toute la détection sur chacune séparément.
    """
    series = query_range(promql, minutes=config.WINDOW_MINUTES)
    per_instance = extract_series(series)

    signals = []
    for entry in per_instance:
        instance, job, values = entry["instance"], entry["job"], entry["values"]
        if not values:
            continue  # pas de données pour cette instance -> on ignore silencieusement

        current_value = values[-1]
        history = values[:-1]

        is_anomaly, zscore = is_statistical_anomaly(history, current_value)
        is_degrading, slope = detect_progressive_degradation(values)
        absolute_breach, threshold = threshold_tracker.check(
            name, instance, current_value, config.THRESHOLDS
        )

        signals.append(MetricSignal(
            name=name,
            instance=instance,
            job=job,
            is_anomaly=is_anomaly,
            zscore=zscore,
            is_degrading=is_degrading,
            slope_per_minute=slope,
            absolute_breach=absolute_breach,
            absolute_threshold=threshold,
            current_value=current_value,
        ))
    return signals


def build_event(incident) -> dict:
    """
    Construit l'événement interne JSON (US 1.2, critères 2 et 3).

    Limite connue : ce module (métriques node_exporter) identifie le
    SERVEUR concerné ("node"), pas le pod applicatif fautif. Le champ
    "service" est laissé à None ici — il sera rempli par le module Epic 2
    (llm-log-analyzer), qui recevra ce "node" en --context, lira les logs
    Loki/Promtail de ce nœud sur la période de l'alerte, et déterminera
    quel pod/service est réellement en cause via affected_service.
    """
    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "severity": incident.severity,
        "service": None,            # rempli plus tard par Epic 2 (analyse des logs)
        "node": incident.instance,  # anciennement nommé "pod" à tort — c'est un serveur, pas un pod
        "metrics": [
            {
                "name": s.name,
                "value": round(s.current_value, 2),
                "zscore": round(s.zscore, 2) if s.zscore is not None else None,
                "is_statistical_anomaly": s.is_anomaly,
                "is_degrading": s.is_degrading,
                "slope_per_minute": round(s.slope_per_minute, 3),
                "absolute_threshold_breached": s.absolute_breach,
                "absolute_threshold_value": s.absolute_threshold if s.absolute_breach else None,
            }
            for s in incident.signals if s.triggered
        ],
    }


def run_once(deduplicator: Deduplicator):
    """Exécute un cycle complet de détection sur toutes les métriques surveillées."""
    all_signals = []
    for name, promql in METRIC_QUERIES.items():
        try:
            all_signals.extend(check_metric(name, promql))
        except Exception as e:
            # Erreur de connexion Prometheus -> sévérité ERROR (US 1.1, critère 3)
            # (le détail de la requête est déjà journalisé dans prometheus_client)
            logger.error("Échec de la récupération de la métrique '%s' : %s", name, e)

    incidents = correlate_by_instance(all_signals)
    active_incidents = [i for i in incidents if i.is_incident]

    if not active_incidents:
        logger.info("Aucune anomalie détectée sur %d instance(s).", len(incidents))
        return

    for incident in active_incidents:
        incident_key = f"{incident.instance}:" + "+".join(
            sorted(s.name for s in incident.signals if s.triggered)
        )

        if deduplicator.should_alert(incident_key):
            event = build_event(incident)
            logger.warning(
                "ALERTE [%s] %s — %s",
                incident.severity.upper(), incident.instance, incident.summary(),
            )
            # Événement JSON structuré, prêt pour publication (prochain ticket : SQS)
            print(json.dumps(event, ensure_ascii=False))
        else:
            logger.info(
                "Incident déjà signalé récemment sur %s, on l'ignore (%s)",
                incident.instance, incident.summary(),
            )


# Instances partagées entre cycles (état nécessaire pour la déduplication et
# le suivi de durée des seuils absolus).
threshold_tracker = ThresholdTracker()


def main():
    logger.info("Démarrage du module de détection d'anomalies")
    logger.info("Prometheus : %s", config.PROMETHEUS_URL)
    logger.info("Intervalle de vérification : %ds", config.POLL_INTERVAL_SECONDS)
    logger.info("Fenêtre d'historique analysée : %d min", config.WINDOW_MINUTES)

    deduplicator = Deduplicator()

    try:
        while True:
            run_once(deduplicator)
            deduplicator.cleanup_expired()
            time.sleep(config.POLL_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        logger.info("Arrêt du module de détection.")
        sys.exit(0)


if __name__ == "__main__":
    main()