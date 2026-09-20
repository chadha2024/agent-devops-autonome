# Module d'analyse des logs par LLM — Agent DevOps

Ce module récupère les logs applicatifs stockés dans Loki (filtrés sur les
niveaux WARN/ERROR/FATAL, 500 dernières lignes du pod concerné), les envoie
à Gemini (Google AI) pour analyse, et produit un diagnostic structuré :
cause probable, service concerné, catégorie d'erreur connue, action
recommandée, avec un statut d'intervention humaine si la confiance est
insuffisante.

## Ce qu'il fait

1. **Agrégation des logs contextuels** : récupère jusqu'à 500 lignes de logs du pod concerné, filtrées sur les niveaux WARN/ERROR/FATAL, en moins de 5 secondes
2. **Extraction des erreurs** : le LLM lit les logs bruts et repère les messages pertinents
3. **Identification de la cause racine** : catégorise l'erreur parmi une liste connue (ex: `OutOfMemory`, `DiskFull`, `NetworkTimeout`) avec un score de confiance
4. **Corrélation temporelle** : situe quand le problème a commencé
5. **Diagnostic structuré** : sortie en JSON exploitable par le futur orchestrateur, avec un flag `needs_human_review` si la confiance est sous le seuil configuré

## Prérequis

- Python 3.10+
- Une clé API Google AI (Gemini) (voir ci-dessous si tu n'en as pas encore)
- Accès à Loki (port-forward en local, direct une fois en pod)

## Si tu n'as pas encore de clé API

Deux options :
1. Demande une clé à ton encadrant (accès au compte Google AI de l'entreprise, si disponible)
2. Crée un compte de test toi-même sur https://aistudio.google.com/apikey
   (un petit crédit gratuit est généralement offert à la création du compte)

## Installation

```powershell
cd llm-log-analyzer
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Configuration de la clé API

**Ne jamais écrire la clé directement dans le code.** Définis-la comme
variable d'environnement, dans la même fenêtre PowerShell où tu vas lancer
le script :

```powershell
$env:GEMINI_API_KEY="ta_clé_ici"
```

(Optionnel) Tu peux aussi choisir un autre modèle Gemini que celui par
défaut (`gemini-3.6-flash`) via :

```powershell
$env:GEMINI_MODEL="gemini-3.1-pro"
```

## Paramètres de récupération et de classification (Sprint 2)

Ces paramètres sont dans `config.py` (pas de variable d'environnement dédiée
pour l'instant, à ajuster directement dans le fichier si besoin) :

| Paramètre | Valeur par défaut | Rôle |
|---|---|---|
| `MAX_LOG_LINES` | `500` | Nombre max de lignes de logs récupérées pour le pod analysé |
| `LOG_LEVELS` | `["WARN", "ERROR", "FATAL"]` | Niveaux conservés lors de la récupération (filtre LogQL) |
| `LOKI_REQUEST_TIMEOUT_SECONDS` | `5` | Budget de temps pour la récupération des logs ; un dépassement (mais réponse obtenue) est logué en warning |
| `ERROR_CATEGORIES` | `OutOfMemory`, `DiskFull`, `NetworkTimeout`, `CrashLoopBackOff`, `ConfigError`, `PermissionDenied`, `Unknown` | Taxonomie fermée utilisée pour classer l'erreur ; toute valeur hors liste renvoyée par le LLM retombe sur `Unknown` |
| `CONFIDENCE_THRESHOLD_PERCENT` | `80` | En dessous de ce seuil, `needs_human_review` passe à `true` dans le diagnostic |

**Note sur la classification** : la catégorisation est faite par le LLM
(Gemini), contraint par le prompt système à choisir uniquement dans
`ERROR_CATEGORIES`, avec une validation supplémentaire côté code (toute
catégorie hors liste est remplacée par `"Unknown"`). Ce choix a été retenu
plutôt qu'une classification par expressions régulières pures pour rester
flexible face à des formulations de logs variées ; une couche regex de
pré-filtrage rapide (avant l'appel LLM) peut être ajoutée en complément si
besoin de réduire la latence/coût sur les cas évidents.

## Avant de lancer : ouvre l'accès à Loki

```powershell
kubectl port-forward -n monitoring svc/loki 3100:3100
$env:LOKI_URL="http://localhost:3100"
```

## Lancer une analyse

```powershell
python main.py --namespace monitoring
```

Options disponibles :
```powershell
python main.py --namespace monitoring --pod loki --minutes 30
python main.py --namespace monitoring --context "Le module de détection a signalé une anomalie CPU à 14h32"
```

## Exemple de sortie

```json
{
  "has_error": true,
  "root_cause": "Le pod redémarre en boucle suite à un échec de connexion à la base de données",
  "affected_service": "api-payment",
  "error_category": "CrashLoopBackOff",
  "error_pattern": "connexion refusée à la base de données",
  "first_occurrence": "2026-08-06T14:02:00Z",
  "confidence_percent": 85,
  "needs_human_review": false,
  "recommended_action": "Vérifier la disponibilité du service RDS et les credentials du pod",
  "raw_summary": "Le service api-payment ne parvient plus à se connecter à la base de données depuis 14h02.",
  "related_log_lines": ["ERROR: connection refused to db:5432", "..."]
}
```

Si `confidence_percent` est sous le seuil (`CONFIDENCE_THRESHOLD_PERCENT`,
80% par défaut), `needs_human_review` passe à `true` et le script affiche un
avertissement explicite en console invitant à une revue manuelle avant toute
remédiation automatique.

## Lien avec les autres modules

```
Loki (logs)  ──→  [ Ce module : analyse LLM ]  ──→  Diagnostic structuré
                                                          ↓
                                            (prochain ticket : publication
                                             vers SQS pour l'orchestrateur)
```

Ce module peut être appelé indépendamment, ou en complément du module de
détection d'anomalies (ticket précédent) : quand une anomalie statistique
est détectée, on peut passer son contexte ici via `--context` pour guider
l'analyse du LLM vers la bonne période et le bon service.

## Étape finale (déploiement)

Comme pour le module de détection, ce code sera containerisé et déployé
comme pod à la fin du Sprint 1, une fois toute la logique validée.