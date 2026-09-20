# Module RCA — Corrélation d'alertes et Root Cause Analysis

Ce module reçoit les événements produits par `anomaly-detector` (Epic 1)
et `llm-log-analyzer` (Epic 2), les corrèle temporellement, et identifie
la cause racine probable de chaque incident à l'aide d'un graphe de
dépendances entre services/infra/réseau.

## Ce qu'il fait

1. **Corrélation temporelle** : regroupe les alertes qui arrivent dans une
   fenêtre glissante de 5 minutes (configurable) en un seul incident,
   plutôt que de les traiter comme des événements indépendants
2. **Graphe de dépendances** (`service_graph.yaml`) : modélise quels
   services/briques infra dépendent de quoi
3. **Propagation de l'impact** : à partir d'une entité en panne, calcule
   qui est potentiellement affecté en aval (transitivement)
4. **Cause racine** : parmi les entités en alerte d'un même incident,
   désigne celle qui est la plus "en amont" du graphe — donc la plus
   probable d'être la cause plutôt que la conséquence — avec un score de
   confiance et une catégorie (`service` / `infra` / `network`)

## ⚠️ Le graphe de dépendances est un exemple à valider

`service_graph.yaml` est actuellement rempli avec les composants réels
mentionnés dans le README Terraform du projet (EKS, RDS PostgreSQL, SQS)
et les deux modules déjà construits (anomaly-detector, llm-log-analyzer).
**Si l'agent doit aussi surveiller d'autres applications derrière ces
briques, il faut les ajouter au fichier avec le même format.**

## Comment la cause racine est choisie (logique)

```
eks-node-group tombe
      │
      ├──→ prometheus tombe (dépend de eks-node-group)
      │         │
      │         └──→ anomaly-detector tombe (dépend de prometheus)
      │
Les 3 alertes arrivent dans la même fenêtre de 5 min → 1 seul incident corrélé
Cause racine = eks-node-group (aucune de ses dépendances n'est elle-même en alerte)
Confiance = 100% (les 3 entités sont cohérentes avec la propagation depuis eks-node-group)
```

Si deux entités en alerte n'ont **aucun** lien de dépendance entre elles
(incidents simultanés mais indépendants), le module choisit quand même une
cause racine unique (comme demandé), mais **signale explicitement** les
entités non expliquées (`unexplained_entities`) plutôt que de prétendre à
tort que tout est lié.

## Installation

```bash
cd rca-module
pip install -r requirements.txt
```

## Lancer les tests

```bash
PYTHONPATH=. pytest tests/ -v
```

## Utilisation (mode fichier, en attendant le branchement SQS)

```bash
python main.py events.jsonl
```

où `events.jsonl` contient un événement JSON par ligne (mélange possible
d'événements `anomaly-detector` et de diagnostics `llm-log-analyzer`).

## Exemple de sortie

```json
[
  {
    "root_cause_entity": "eks-node-group",
    "root_cause_type": "infra",
    "confidence_percent": 100,
    "correlated_alert_count": 3,
    "affected_entities": ["anomaly-detector", "eks-node-group", "prometheus"],
    "explained_by_root_cause": ["anomaly-detector", "eks-node-group", "prometheus"],
    "unexplained_entities": [],
    "incident_start": "2026-08-11T12:00:00+00:00",
    "incident_end": "2026-08-11T12:02:00+00:00"
  }
]
```

## Prochaine étape (ticket suivant)

Actuellement, ce module lit les événements depuis un fichier `.jsonl`. La
prochaine étape est de le brancher sur la **queue SQS** (déjà provisionnée
en Terraform) pour consommer les événements d'anomaly-detector et
llm-log-analyzer en temps réel, plutôt qu'en différé sur fichier.

Le résultat RCA (cause racine + confiance + type) est ensuite ce qui
alimentera le ticket suivant : **classification de sévérité et
priorisation des incidents**.
