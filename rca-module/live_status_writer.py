"""
Écriture de l'état en temps réel, pour le dashboard live (live_dashboard.html).

Écrit un petit fichier JSON à chaque étape de live_demo.py. Le dashboard
HTML relit ce fichier automatiquement toutes les secondes (via fetch côté
navigateur) pour se mettre à jour sans rien recharger manuellement.
"""

import json
import time

STEPS_ORDER = ["Alerte", "RCA", "Sévérité", "Décision", "Guardrails", "Approbation", "Exécution", "Vérification"]


class LiveStatusWriter:
    def __init__(self, path: str = "live_status.json"):
        self.path = path
        self.state = {
            "entity": None,
            "node_status": "healthy",  # healthy | alert | waiting | remediating | recovered | failed
            "current_step": None,
            "steps": {name: "pending" for name in STEPS_ORDER},
            "details": {},
            "log": [],              # historique des événements, le plus récent en dernier
            "watching": True,        # le processus de surveillance tourne-t-il encore ?
            "awaiting_choice": False,
            "candidates": [],
            "updated_at": time.time(),
        }
        self._write()

    def _write(self) -> None:
        self.state["updated_at"] = time.time()
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False)

    def log_event(self, message: str) -> None:
        entry = {"time": time.strftime("%H:%M:%S"), "message": message}
        self.state["log"].append(entry)
        self.state["log"] = self.state["log"][-30:]  # garde les 30 derniers, pas de fichier qui grossit à l'infini
        self._write()

    def watching_idle(self, entity: str) -> None:
        """Rien d'anormal détecté : remet le nœud en sain, reste en
        surveillance. Ne réinitialise PAS les étapes — la dernière
        chaîne de remédiation reste visible (en vert) jusqu'au prochain
        incident, plutôt que de disparaître aussitôt le retour au calme."""
        self.state["entity"] = entity
        self.state["node_status"] = "healthy"
        self.state["current_step"] = None
        self._write()

    def start(self, entity: str) -> None:
        self.state["entity"] = entity
        self.state["node_status"] = "alert"
        self._write()

    def step(self, name: str, status: str = "done", detail: str | None = None) -> None:
        self.state["current_step"] = name
        self.state["steps"][name] = status
        if detail:
            self.state["details"][name] = detail
        self._write()

    def set_node_status(self, status: str) -> None:
        self.state["node_status"] = status
        self._write()

    def finish(self, success: bool) -> None:
        self.state["node_status"] = "recovered" if success else "failed"
        self.state["current_step"] = None
        self._write()

    def stopped(self) -> None:
        self.state["watching"] = False
        self._write()

    def await_human_choice(self, candidates: list[dict]) -> None:
        """Affiche des options cliquables dans le dashboard et met le
        pipeline en pause, en attendant qu'un humain choisisse."""
        self.state["node_status"] = "waiting"
        self.state["awaiting_choice"] = True
        self.state["candidates"] = candidates
        self._write()

    def choice_received(self, action_id: str) -> None:
        self.state["awaiting_choice"] = False
        self.state["candidates"] = []
        self.log_event(f"Choix humain reçu : {action_id}")
