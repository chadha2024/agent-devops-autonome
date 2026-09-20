"""
Module RCA — point d'entrée principal.

Reçoit des événements issus d'Epic 1 (anomaly-detector) et Epic 2
(llm-log-analyzer), les convertit en alertes unifiées, les corrèle
temporellement, puis identifie la cause racine de chaque incident corrélé.

Usage prévu (une fois branché à SQS, cf. TODO) :
    python main.py --events events.jsonl

Pour l'instant, ce fichier expose surtout `analyze_events()`, pensé pour
être appelé par le futur consommateur SQS plutôt que lancé seul en boucle
(contrairement à anomaly-detector et llm-log-analyzer, ce module réagit à
des événements, il ne poll rien lui-même).
"""

import json
import sys

from alert_model import Alert, from_epic1_event, from_epic2_diagnostic
from alert_correlator import TemporalCorrelator, DEFAULT_CORRELATION_WINDOW_SECONDS
from rca_engine import RCAEngine
from service_graph import ServiceGraph
from logging_setup import get_logger

logger = get_logger(__name__)


def analyze_events(raw_events: list[dict], graph: ServiceGraph,
                    window_seconds: int = DEFAULT_CORRELATION_WINDOW_SECONDS) -> list[dict]:
    """
    Pipeline complet : événements bruts -> alertes unifiées -> corrélation
    temporelle -> RCA. Retourne une liste de résultats JSON, un par
    incident corrélé.
    """
    alerts: list[Alert] = []
    for event in raw_events:
        alert = _to_alert(event, graph)
        if alert:
            alerts.append(alert)
        else:
            logger.warning("Événement ignoré, source non reconnue ou incomplète : %s", event)

    if not alerts:
        logger.info("Aucune alerte exploitable reçue.")
        return []

    correlator = TemporalCorrelator(window_seconds=window_seconds)
    groups = correlator.correlate(alerts)

    engine = RCAEngine(graph)
    results = []
    for group in groups:
        result = engine.analyze(group)
        results.append(result.to_dict())
        logger.warning(
            "INCIDENT : cause racine probable = %s (%s), confiance=%d%%, %d alerte(s) corrélée(s)",
            result.root_cause_entity, result.root_cause_type,
            result.confidence_percent, result.correlated_alert_count,
        )

    return results


def _to_alert(event: dict, graph: ServiceGraph) -> Alert | None:
    """
    Distingue un événement Epic 1 d'un diagnostic Epic 2 par leurs champs
    caractéristiques respectifs, faute d'un champ "source" explicite côté
    upstream (à ajouter dans un futur ticket pour éviter cette heuristique).
    """
    if "node" in event and "metrics" in event:
        return from_epic1_event(event, graph)
    if "has_error" in event and "error_category" in event:
        return from_epic2_diagnostic(event)
    return None


def main():
    """
    Lit une liste d'événements JSON (un par ligne, format .jsonl) depuis un
    fichier passé en argument, et affiche les résultats RCA.

    TODO (prochain ticket) : remplacer cette lecture fichier par une
    consommation en continu depuis SQS (déjà provisionné en Terraform),
    qui recevra les événements publiés par anomaly-detector et
    llm-log-analyzer en temps réel.
    """
    if len(sys.argv) < 2:
        print("Usage : python main.py <fichier_evenements.jsonl>")
        sys.exit(1)

    graph = ServiceGraph(path="service_graph.yaml")

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        raw_events = [json.loads(line) for line in f if line.strip()]

    results = analyze_events(raw_events, graph)

    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
