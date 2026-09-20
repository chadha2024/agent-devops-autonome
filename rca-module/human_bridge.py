"""
Pont humain — met le pipeline en pause et attend qu'un choix arrive
depuis le dashboard (via live_server.py, qui écrit human_choice.json
quand un bouton est cliqué dans le navigateur).

Utilisé pour 2 cas différents dans live_demo.py :
  1. Choisir UNE action parmi plusieurs candidates (aucune remédiation
     automatique disponible, ou bloquée par les guardrails)
  2. Répondre à une question Approuver/Rejeter (US 4.2) — dans ce cas,
     les "candidates" sont juste [{"action_id": "approve", ...},
     {"action_id": "reject", ...}]
"""

import json
import os
import time

from logging_setup import get_logger

logger = get_logger(__name__)

CHOICE_PATH = "human_choice.json"


def wait_for_human_choice(candidates: list[dict], live, timeout_seconds: int = 300,
                           poll_seconds: float = 1.0) -> str | None:
    """
    Bloque jusqu'à ce qu'un choix soit reçu (ou que le délai expire).
    Retourne l'action_id choisi, ou None si personne n'a répondu à temps.
    """
    if os.path.exists(CHOICE_PATH):
        os.remove(CHOICE_PATH)  # nettoie un éventuel choix périmé d'une session précédente

    live.await_human_choice(candidates)
    live.log_event("En attente d'un choix humain (dashboard)...")

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if os.path.exists(CHOICE_PATH):
            try:
                with open(CHOICE_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                os.remove(CHOICE_PATH)
                action_id = data.get("action_id")
                live.choice_received(action_id)
                return action_id
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Fichier de choix illisible, on continue d'attendre : %s", e)
        time.sleep(poll_seconds)

    live.log_event("Timeout : aucun choix humain reçu")
    return None
