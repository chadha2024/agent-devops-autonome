"""
Suivi de durée pour les seuils critiques absolus (US 1.2, critère 1).

Avant : thresholds.yaml était chargé dans config.THRESHOLDS mais jamais
utilisé nulle part — seule la détection statistique (z-score) déclenchait
des alertes. Les seuils absolus (ex: "CPU > 90% pendant 5 minutes") n'étaient
donc jamais réellement vérifiés.

Ce module comble ce trou : pour chaque (métrique, instance), on mémorise
depuis quand la valeur dépasse le seuil critique. Le seuil n'est considéré
franchi que si le dépassement est SOUTENU pendant au moins `duration_seconds`
(cf. thresholds.yaml) — exactement comme prévu par le format du fichier YAML,
pour éviter de déclencher une alerte sur un simple pic transitoire.
"""

import time


class ThresholdTracker:
    def __init__(self):
        # clé "metric:instance" -> timestamp du premier dépassement observé
        self._breach_start: dict[str, float] = {}

    def check(self, metric_name: str, instance: str, current_value: float, thresholds: dict) -> tuple[bool, float]:
        """
        Retourne (seuil_absolu_franchi, valeur_seuil_critique).

        `thresholds` est l'entrée de thresholds.yaml pour cette métrique,
        ex: {"critical": 90, "duration_seconds": 300}.
        """
        if metric_name not in thresholds:
            return False, 0.0

        critical = thresholds[metric_name]["critical"]
        duration_required = thresholds[metric_name]["duration_seconds"]
        key = f"{metric_name}:{instance}"
        now = time.time()

        if current_value > critical:
            if key not in self._breach_start:
                self._breach_start[key] = now  # premier dépassement observé
            elapsed = now - self._breach_start[key]
            if elapsed >= duration_required:
                return True, critical
            return False, critical  # dépassé, mais pas encore assez longtemps
        else:
            self._breach_start.pop(key, None)  # retour à la normale, on réinitialise
            return False, critical