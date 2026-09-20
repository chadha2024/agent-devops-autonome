"""
Client Loki : récupère les logs bruts qui seront envoyés au LLM pour analyse.
"""

import logging
import time
import requests
from config import (
    LOKI_URL,
    LOG_LOOKBACK_MINUTES,
    MAX_LOG_LINES,
    LOG_LEVELS,
    LOKI_REQUEST_TIMEOUT_SECONDS,
)

logger = logging.getLogger(__name__)


def fetch_logs(namespace: str, minutes: int = LOG_LOOKBACK_MINUTES, pod: str | None = None) -> list[str]:
    """
    Récupère les logs bruts d'un namespace (et optionnellement d'un pod
    précis) sur les `minutes` dernières minutes.

    US 2.1 :
      - ne conserve que les niveaux WARN / ERROR / FATAL (filtre de ligne LogQL)
      - limite au 500 dernières lignes (MAX_LOG_LINES)
      - respecte un budget de 5 secondes (timeout HTTP + warning si dépassé)

    Retourne une simple liste de lignes de texte (les messages de log),
    la plus récente en dernier.
    """
    end_ns = int(time.time() * 1e9)
    start_ns = end_ns - int(minutes * 60 * 1e9)

    selector = f'{{namespace="{namespace}"}}'
    if pod:
        selector = f'{{namespace="{namespace}", pod=~"{pod}.*"}}'

    # Filtre de ligne LogQL : ne garde que WARN/ERROR/FATAL (regex, insensible
    # à la casse). Si les logs sont structurés en JSON avec un champ "level",
    # remplacer par un filtre de label/JSON (`| json | level=~...`) selon le
    # format réel produit par les pods.
    level_regex = "|".join(LOG_LEVELS)
    query = f'{selector} |~ "(?i)({level_regex})"'

    start_time = time.monotonic()
    try:
        resp = requests.get(
            f"{LOKI_URL}/loki/api/v1/query_range",
            params={
                "query": query,
                "start": start_ns,
                "end": end_ns,
                "limit": MAX_LOG_LINES,
                "direction": "backward",  # les plus récents d'abord
            },
            timeout=LOKI_REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
    finally:
        elapsed = time.monotonic() - start_time
        if elapsed > LOKI_REQUEST_TIMEOUT_SECONDS:
            logger.warning(
                "Récupération des logs Loki plus lente que le budget de %ss (%.2fs, namespace=%s, pod=%s)",
                LOKI_REQUEST_TIMEOUT_SECONDS, elapsed, namespace, pod,
            )

    lines = []
    for stream in data.get("data", {}).get("result", []):
        for entry in stream["values"]:
            # entry = [timestamp_ns, "le message de log"]
            lines.append(entry[1])

    # On remet dans l'ordre chronologique (plus ancien -> plus récent),
    # plus naturel à lire pour le LLM qui doit repérer une chronologie
    lines.reverse()
    return lines