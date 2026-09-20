"""
Configuration centralisée du logging (US 1.1, critère 3).

Avant : les erreurs étaient affichées avec print(f"[WARN] ...") ou
print(f"[INFO] ...") — du texte libre, sans vrai niveau de sévérité,
non exploitable par un système de collecte de logs (ex: le module Loki
du ticket suivant, qui lira justement ces logs).

Maintenant : on utilise le module standard `logging`, avec un vrai niveau
ERROR pour les erreurs de connexion, comme l'exige le critère d'acceptation.
Le format inclut un timestamp ISO et le niveau, pour être facilement
parsable par un agrégateur de logs (Loki, ELK, etc.).
"""

import logging
import sys

from config import LOG_LEVEL


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:  # évite les handlers dupliqués si appelé plusieurs fois
        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
    return logger