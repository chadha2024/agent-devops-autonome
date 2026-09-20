"""
Client pour interroger l'API HTTP de Prometheus (PromQL).

Deux types de requêtes :
- query_instant  : la valeur actuelle d'une métrique
- query_range    : l'historique d'une métrique sur une période (nécessaire
                    pour calculer z-score, moyenne mobile, tendance)
"""

import requests
import time
from config import (
    PROMETHEUS_URL,
    PROMETHEUS_BEARER_TOKEN,
    PROMETHEUS_USERNAME,
    PROMETHEUS_PASSWORD,
)
from logging_setup import get_logger

logger = get_logger(__name__)

# Le port-forward kubectl ajoute de la latence réseau (PC -> AWS -> pod),
# donc on tolère un délai plus long et on retente plusieurs fois avant d'abandonner.
REQUEST_TIMEOUT = 30
MAX_RETRIES = 3
RETRY_BASE_DELAY_SECONDS = 2  # backoff exponentiel : 2s, 4s, 8s...


def _build_auth_kwargs() -> dict:
    """
    Construit les kwargs requests nécessaires à l'authentification
    (US 1.1, critère 1), selon ce qui est configuré :
      1. Bearer token -> header Authorization
      2. Basic Auth   -> tuple (user, password)
      3. Rien de configuré -> requête non authentifiée (dev local)
    """
    if PROMETHEUS_BEARER_TOKEN:
        return {"headers": {"Authorization": f"Bearer {PROMETHEUS_BEARER_TOKEN}"}}
    if PROMETHEUS_USERNAME and PROMETHEUS_PASSWORD:
        return {"auth": (PROMETHEUS_USERNAME, PROMETHEUS_PASSWORD)}
    return {}


def _get_with_retry(url: str, params: dict):
    """
    Fait une requête HTTP authentifiée avec backoff exponentiel en cas
    d'échec. Toute erreur de connexion est journalisée en sévérité ERROR
    (US 1.1, critère 3) avant d'être relancée pour que l'appelant décide
    quoi en faire.
    """
    auth_kwargs = _build_auth_kwargs()
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, timeout=REQUEST_TIMEOUT, **auth_kwargs)
            resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException as e:
            last_error = e
            if attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "Requête Prometheus échouée (tentative %d/%d) : %s — nouvel essai dans %ds",
                    attempt, MAX_RETRIES, e, delay,
                )
                time.sleep(delay)

    # Toutes les tentatives ont échoué : c'est une vraie erreur de connexion,
    # journalisée en ERROR comme l'exige le critère d'acceptation.
    logger.error(
        "Connexion à Prometheus impossible après %d tentatives (url=%s) : %s",
        MAX_RETRIES, url, last_error,
    )
    raise last_error


def query_instant(promql: str):
    """Retourne la valeur actuelle d'une requête PromQL."""
    resp = _get_with_retry(
        f"{PROMETHEUS_URL}/api/v1/query",
        params={"query": promql},
    )
    result = resp.json()["data"]["result"]
    return result  # liste de séries, chacune avec ses labels + valeur


def query_range(promql: str, minutes: int, step_seconds: int = 30):
    """
    Retourne l'historique d'une métrique sur les `minutes` dernières minutes.
    Utilisé pour le calcul du z-score et de la moyenne mobile, qui ont
    besoin de plusieurs points dans le temps, pas juste la valeur actuelle.
    """
    end = time.time()
    start = end - (minutes * 60)

    resp = _get_with_retry(
        f"{PROMETHEUS_URL}/api/v1/query_range",
        params={
            "query": promql,
            "start": start,
            "end": end,
            "step": f"{step_seconds}s",
        },
    )
    result = resp.json()["data"]["result"]
    return result  # liste de séries, chacune avec labels ("metric") + valeurs


def extract_series(series_result):
    """
    Extrait CHAQUE série (une par instance/pod), au lieu de ne garder que
    la première comme le faisait l'ancienne `extract_values`.

    Corrige un bug important : les requêtes PromQL groupent `by (instance)`,
    donc Prometheus renvoie une série par nœud/pod. L'ancienne implémentation
    ignorait silencieusement toutes les séries sauf la première — un agent
    multi-pod ratait donc la majorité des incidents.

    Retourne une liste de dicts :
        [{"instance": "10.0.1.5:9100", "job": "node-exporter",
          "values": [1.2, 1.3, ...]}, ...]
    """
    series_list = []
    for series in series_result:
        labels = series.get("metric", {})
        # `instance` est le label le plus fiable pour identifier la cible
        # (host:port). On garde aussi `job` s'il est présent, pour enrichir
        # le futur événement JSON (US 1.2, critère 3 : service concerné).
        identifier = labels.get("instance") or labels.get("pod") or "unknown"
        job = labels.get("job", "unknown")
        values = [float(v[1]) for v in series["values"]]
        series_list.append({"instance": identifier, "job": job, "values": values})
    return series_list