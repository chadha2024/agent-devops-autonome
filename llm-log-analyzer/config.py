"""
Configuration du module d'analyse des logs par LLM.
"""

import os

# ---------------------------------------------------------------------------
# Connexion à Loki (source des logs à analyser)
# ---------------------------------------------------------------------------
# En local (sur ton PC) : nécessite un port-forward, ex.
#   kubectl port-forward -n monitoring svc/loki 3100:3100
#   -> LOKI_URL=http://localhost:3100
#
# Une fois déployé comme pod dans le cluster : adresse interne directe.
LOKI_URL = os.environ.get(
    "LOKI_URL",
    "http://loki.monitoring.svc.cluster.local:3100",
)

# ---------------------------------------------------------------------------
# Connexion à Gemini (Google AI)
# ---------------------------------------------------------------------------
# La clé doit être fournie via une variable d'environnement, jamais écrite
# en dur dans le code (même logique que le mot de passe RDS dans Terraform).
# Récupérable sur https://aistudio.google.com/apikey
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Modèle utilisé pour l'analyse des logs
# gemini-3.6-flash : modèle stable (GA) au moment de l'écriture, bon rapport
# vitesse/coût pour ce genre de tâche. À ajuster si besoin de plus de
# puissance de raisonnement (ex: gemini-3.1-pro).
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")

# ---------------------------------------------------------------------------
# Paramètres de la récupération des logs (US 2.1)
# ---------------------------------------------------------------------------
LOG_LOOKBACK_MINUTES = 15   # combien de temps de logs on analyse en arrière
MAX_LOG_LINES = 500          # US 2.1 : les 500 dernières lignes du pod en erreur

# US 2.1 : on ne conserve que les niveaux pertinents pour le diagnostic.
# Utilisé comme filtre de ligne LogQL (regex, insensible à la casse).
LOG_LEVELS = ["WARN", "ERROR", "FATAL"]

# US 2.1 : la récupération des logs ne doit pas dépasser 5 secondes.
# Utilisé à la fois comme timeout HTTP et comme seuil de log/alerte si
# l'appel a été plus lent que prévu (mais a quand même abouti).
LOKI_REQUEST_TIMEOUT_SECONDS = 5

# ---------------------------------------------------------------------------
# Paramètres de classification de l'erreur (US 2.2)
# ---------------------------------------------------------------------------
# Taxonomie fermée : le diagnostic doit rattacher l'erreur à l'une de ces
# catégories connues (ou "Unknown" si aucune ne correspond), plutôt qu'à
# une description libre non exploitable par l'orchestrateur.
ERROR_CATEGORIES = [
    "OutOfMemory",
    "DiskFull",
    "NetworkTimeout",
    "CrashLoopBackOff",
    "ConfigError",
    "PermissionDenied",
    "Unknown",
]

# US 2.2 : en dessous de ce seuil de confiance, l'agent ne doit pas décider
# seul de la remédiation et doit demander une intervention humaine.
CONFIDENCE_THRESHOLD_PERCENT = 80