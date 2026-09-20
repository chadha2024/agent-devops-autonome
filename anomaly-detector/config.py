"""
Configuration centralisée du module de détection d'anomalies.
Toutes les valeurs ajustables sont ici, pour ne pas avoir à fouiller
dans le reste du code pour changer un seuil.
"""

import yaml
import os

# ---------------------------------------------------------------------------
# Connexion à Prometheus
# ---------------------------------------------------------------------------
# En local, on accède à Prometheus via un port-forward Kubernetes :
#   kubectl port-forward -n monitoring svc/monitoring-kube-prometheus-prometheus 9090:9090
PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://localhost:9090")

# --- Authentification (US 1.1, critère 1) ---------------------------------
# Trois méthodes supportées, choisies automatiquement selon ce qui est
# renseigné dans l'environnement (aucune n'est écrite en dur dans le code) :
#   1. Bearer token (ex: reverse-proxy protégé par token, Thanos, Cortex)
#   2. Basic Auth (utilisateur/mot de passe)
#   3. Aucune (dev local avec port-forward, Prometheus non protégé)
PROMETHEUS_BEARER_TOKEN = os.environ.get("PROMETHEUS_BEARER_TOKEN")
PROMETHEUS_USERNAME = os.environ.get("PROMETHEUS_USERNAME")
PROMETHEUS_PASSWORD = os.environ.get("PROMETHEUS_PASSWORD")

# ---------------------------------------------------------------------------
# Paramètres de la boucle de détection
# ---------------------------------------------------------------------------
POLL_INTERVAL_SECONDS = 30      # fréquence à laquelle on interroge Prometheus
WINDOW_MINUTES = 15              # fenêtre d'historique utilisée pour calculer les stats
Z_SCORE_THRESHOLD = 3.0          # au-delà de 3 écarts-types = anomalie statistique
MOVING_AVERAGE_WINDOW = 5        # nombre de points pour lisser la courbe

# ---------------------------------------------------------------------------
# Détection de dégradation progressive
# ---------------------------------------------------------------------------
DEGRADATION_MIN_SLOPE_PERCENT_PER_MIN = 0.5  # ex: mémoire qui monte de 0.5%/min en continu

# ---------------------------------------------------------------------------
# Déduplication des alertes (réduction du bruit)
# ---------------------------------------------------------------------------
DEDUP_COOLDOWN_SECONDS = 300     # 5 minutes avant de re-signaler la même anomalie

# ---------------------------------------------------------------------------
# Seuils critiques absolus (chargés depuis thresholds.yaml, US 1.2)
# Ces seuils servent de garde-fou en complément de la détection statistique :
# même si le z-score ne détecte rien (comportement "normal" pour ce système),
# un dépassement de ces seuils absolus reste toujours signalé, à condition
# d'être soutenu pendant au moins `duration_seconds` (cf. threshold_tracker.py).
# ---------------------------------------------------------------------------
def load_thresholds(path="thresholds.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["thresholds"]


THRESHOLDS = load_thresholds()

# ---------------------------------------------------------------------------
# Logging (US 1.1, critère 3 : erreurs de connexion en sévérité ERROR)
# ---------------------------------------------------------------------------
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")