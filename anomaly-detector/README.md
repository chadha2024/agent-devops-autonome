# Module de détection d'anomalies — Agent DevOps

Ce module surveille en continu les métriques CPU, mémoire et réseau d'un
cluster Kubernetes, collectées par Prometheus (via node_exporter), et
détecte les comportements anormaux via trois mécanismes complémentaires :
seuils critiques absolus, anomalies statistiques (z-score) et dégradation
progressive.

## Ce qu'il fait

1. **Seuils critiques absolus** (`thresholds.yaml`) : déclenche une alerte
   quand une métrique dépasse un seuil fixe (ex: CPU > 90%) pendant une
   durée soutenue (ex: 5 minutes), pour éviter les faux positifs sur un
   pic court
2. **Anomalies statistiques (z-score)** : détecte une valeur qui s'écarte
   fortement du comportement habituel du système, plutôt qu'un seuil fixe
3. **Dégradation progressive** : détecte une métrique qui monte doucement
   dans le temps (ex: fuite mémoire), via une régression linéaire simple
4. **Corrélation par instance** : chaque serveur (nœud) est analysé
   séparément — une alerte sur `node-2` ne masque jamais ce qui se passe
   sur `node-1`, et inversement
5. **Déduplication** : évite de spammer la même alerte toutes les 30 secondes
6. **Événement JSON structuré** : chaque alerte est émise en JSON, prête à
   être consommée par un autre système (ex: publication vers SQS, ticket
   suivant)

## ⚠️ Limite importante : "node" ≠ "pod"

Ce module utilise **node_exporter**, qui mesure la machine (le nœud)
dans son ensemble, pas chaque pod individuellement. Prometheus n'a donc
aucun moyen de savoir *lequel* des pods tournant sur ce nœud est
responsable d'une surcharge — c'est pour ça que l'événement JSON contient
un champ `"node"` (le serveur concerné), et un champ `"service": null`
laissé volontairement vide.

**L'identification précise du pod/service fautif est déléguée au module
`llm-log-analyzer` (Epic 2)** : celui-ci reçoit le nœud concerné (via
`--context`), lit les logs Loki/Promtail de ce nœud sur la période de
l'alerte, et détermine le service en cause via son champ
`affected_service`.

```
anomaly-detector (ce module)          llm-log-analyzer (module suivant)
───────────────────────────           ──────────────────────────────
"node-2 est en surcharge      ──→     lit les logs de node-2,
CPU (96%, soutenu 5 min)"             trouve que c'est api-payment
{"node": "node-2:9100",               le fautif
 "service": null}                     → affected_service: "api-payment"
```

Si un vrai identifiant de pod est nécessaire directement à cette étape,
il faudrait migrer vers des métriques cAdvisor/kube-state-metrics
(`container_cpu_usage_seconds_total{namespace=..., pod=...}`) — non fait
à ce stade, car ça implique de revoir les requêtes PromQL et les seuils
en profondeur.

## Prérequis

- Python 3.10+ (pour la syntaxe `list[float] | None`)
- Accès à Prometheus via un port-forward (ou accès direct une fois en pod)

## Installation

```powershell
cd anomaly-detector
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Avant de lancer : ouvre l'accès à Prometheus

Dans une fenêtre PowerShell séparée (à garder ouverte pendant l'exécution) :

```powershell
kubectl port-forward -n monitoring svc/monitoring-kube-prometheus-prometheus 9090:9090
```

## Authentification (si Prometheus est protégé)

Aucune clé n'est écrite en dur dans le code. Selon ta configuration,
définis l'une de ces options avant de lancer le script :

```powershell
# Option 1 : Bearer token
$env:PROMETHEUS_BEARER_TOKEN="ton_token_ici"

# Option 2 : Basic Auth
$env:PROMETHEUS_USERNAME="ton_user"
$env:PROMETHEUS_PASSWORD="ton_mot_de_passe"

# Si aucune des deux n'est définie : requêtes non authentifiées
# (cas normal en local avec port-forward sur un Prometheus non protégé)
```

## Lancer le module

```powershell
python main.py
```

Tu devrais voir un affichage toutes les 30 secondes, du type :

```
2026-08-10T11:24:47+0000 [INFO] main: Aucune anomalie détectée sur 3 instance(s).
2026-08-10T11:25:17+0000 [WARNING] main: ALERTE [CRITICAL] node-2:9100 — cpu_percent=seuil_absolu_dépassé(valeur=96.00>90)
{"timestamp": "2026-08-10T11:25:17+0000", "severity": "critical", "service": null, "node": "node-2:9100", "metrics": [{"name": "cpu_percent", "value": 96.0, "zscore": 0.0, "is_statistical_anomaly": false, "is_degrading": true, "slope_per_minute": 2.17, "absolute_threshold_breached": true, "absolute_threshold_value": 90}]}
```

La ligne `[WARNING]`/`[ERROR]` (logs lisibles par un humain) et la ligne
JSON (événement structuré, machine-readable) sont émises séparément et
volontairement redondantes : la première pour surveiller à l'œil, la
seconde pour être consommée automatiquement.

## Configuration

Tous les paramètres ajustables sont dans `config.py` :

| Paramètre | Rôle | Valeur par défaut |
|---|---|---|
| `POLL_INTERVAL_SECONDS` | Fréquence de vérification | 30s |
| `WINDOW_MINUTES` | Historique utilisé pour les stats | 15 min |
| `Z_SCORE_THRESHOLD` | Sensibilité de la détection statistique | 3.0 |
| `DEGRADATION_MIN_SLOPE_PERCENT_PER_MIN` | Sensibilité de la détection de dégradation | 0.5%/min |
| `DEDUP_COOLDOWN_SECONDS` | Délai avant de re-signaler la même alerte | 300s (5 min) |
| `LOG_LEVEL` | Niveau de log affiché (`INFO`, `WARNING`, `ERROR`...) | `INFO` |

Les seuils critiques absolus (`thresholds.yaml`) sont maintenant
**réellement appliqués** (pas seulement chargés) via `threshold_tracker.py`,
avec prise en compte de `duration_seconds` :

| Métrique | Seuil critique | Durée soutenue requise |
|---|---|---|
| CPU | 90% | 5 min |
| RAM | 85% | 5 min |
| Réseau | 10 Mo/s | 5 min |

## Fiabilité réseau

Les requêtes vers Prometheus sont automatiquement réessayées en cas
d'échec, avec un **délai exponentiel** (2s, 4s, 8s). Si toutes les
tentatives échouent, l'erreur est journalisée en sévérité **ERROR**
(et non simplement affichée en texte libre), pour être exploitable par
un système de collecte de logs.

## Tester sans Prometheus (données simulées)

Pour vérifier que la logique de détection fonctionne sans dépendre du
cluster, un test rapide est possible directement en Python :

```python
from detectors import is_statistical_anomaly
history = [20, 22, 21, 19, 20, 21, 20, 22]
anomaly, z = is_statistical_anomaly(history, 95)
print(anomaly, z)  # True, un z-score élevé
```

## Prochaine étape (ticket suivant)

Ce module affiche actuellement les événements JSON dans la console. La
prochaine étape sera de les publier dans la queue **SQS** (déjà créée
dans l'infrastructure Terraform), pour que le futur **orchestrateur**
puisse les recevoir et déclencher le workflow OAPE.

## Étape finale (déploiement)

Une fois validé en local, ce module sera packagé dans une image Docker
et déployé comme pod dans le cluster EKS, comme les autres composants
(Prometheus, Grafana, Loki).