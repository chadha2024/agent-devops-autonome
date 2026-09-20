"""
Client Prometheus minimal pour rca-module.

Volontairement indépendant de prometheus_client.py du module
anomaly-detector : chaque module de l'agent est pensé pour être
containerisé et déployé séparément (cf. README des autres modules), donc
on évite les imports cross-dossiers fragiles. Ici, on n'a besoin que
d'une valeur instantanée, pas de l'historique complet.
"""

import os
import requests

PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")
REQUEST_TIMEOUT = 10

METRIC_QUERIES = {
    "cpu_percent": (
        '100 - (avg by (instance) (rate(node_cpu_seconds_total{{mode="idle"}}[2m]) '
        'and on(instance) up{{instance="{instance}"}}) * 100)'
    ),
    "ram_percent": (
        '100 * (1 - (node_memory_MemAvailable_bytes{{instance="{instance}"}} '
        '/ node_memory_MemTotal_bytes{{instance="{instance}"}}))'
    ),
}


def query_instant(promql: str) -> float | None:
    """Retourne la valeur actuelle d'une requête PromQL, ou None si
    indisponible (pas de donnée, Prometheus injoignable...) — l'appelant
    doit savoir gérer l'absence de mesure plutôt que de planter."""
    try:
        resp = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": promql},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        result = resp.json()["data"]["result"]
        if not result:
            return None
        return float(result[0]["value"][1])
    except (requests.exceptions.RequestException, KeyError, IndexError, ValueError):
        return None


def capture_metrics(instance: str) -> dict[str, float | None]:
    """Capture un instantané des métriques suivies pour un nœud donné."""
    return {
        name: query_instant(query.format(instance=instance))
        for name, query in METRIC_QUERIES.items()
    }
