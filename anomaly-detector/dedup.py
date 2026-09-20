"""
Déduplication (réduction du bruit).

Si un incident dure 20 minutes et qu'on vérifie toutes les 30 secondes,
sans déduplication on générerait 40 alertes identiques. Ce module garde
en mémoire les incidents déjà signalés récemment et n'en re-signale pas
un identique avant la fin du cooldown.
"""

import time
from config import DEDUP_COOLDOWN_SECONDS


class Deduplicator:
    def __init__(self, cooldown_seconds: int = DEDUP_COOLDOWN_SECONDS):
        self.cooldown_seconds = cooldown_seconds
        self._last_seen: dict[str, float] = {}  # clé d'incident -> timestamp dernière alerte

    def should_alert(self, incident_key: str) -> bool:
        """
        Retourne True si cet incident doit être signalé maintenant
        (soit c'est nouveau, soit le cooldown est écoulé depuis la
        dernière fois qu'on l'a signalé).
        """
        now = time.time()
        last_seen = self._last_seen.get(incident_key)

        if last_seen is None or (now - last_seen) > self.cooldown_seconds:
            self._last_seen[incident_key] = now
            return True

        return False  # déjà signalé récemment, on ignore ce doublon

    def cleanup_expired(self):
        """Nettoie les entrées anciennes pour ne pas faire grossir la mémoire indéfiniment."""
        now = time.time()
        expired = [k for k, t in self._last_seen.items() if (now - t) > (self.cooldown_seconds * 4)]
        for k in expired:
            del self._last_seen[k]
