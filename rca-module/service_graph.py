"""
Graphe de dépendances entre services/infra/réseau (Ticket RCA, critère 2).

Charge service_graph.yaml et expose deux opérations essentielles au RCA :
  - upstream_dependencies(entity)  : de quoi cette entité dépend
  - propagate_impact(entity)       : qui est potentiellement affecté SI
                                      cette entité tombe (critère 3)
"""

import yaml


class ServiceGraph:
    def __init__(self, path: str = "service_graph.yaml"):
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        self.entities: dict[str, dict] = data.get("entities", {})

        # Index inverse : qui dépend de moi ? (nécessaire pour la propagation
        # d'impact en aval, sans le recalculer à chaque appel)
        self._dependents: dict[str, list[str]] = {name: [] for name in self.entities}
        for name, info in self.entities.items():
            for dep in info.get("depends_on", []):
                self._dependents.setdefault(dep, []).append(name)

        # Index nœud Prometheus -> service hébergé, pour relier les alertes
        # d'Epic 1 (qui ne connaissent qu'un "node") à une entité du graphe.
        self._by_node: dict[str, str] = {
            info["hosted_on"]: name
            for name, info in self.entities.items()
            if info.get("hosted_on")
        }

    def entity_type(self, entity: str) -> str:
        """Catégorie de l'entité : service / infra / network (critère 4)."""
        return self.entities.get(entity, {}).get("type", "unknown")

    def resolve_from_node(self, node: str) -> str | None:
        """Retrouve le service applicatif hébergé sur un nœud Prometheus donné."""
        return self._by_node.get(node)

    def upstream_dependencies(self, entity: str) -> list[str]:
        """De quoi `entity` dépend directement."""
        return list(self.entities.get(entity, {}).get("depends_on", []))

    def propagate_impact(self, entity: str) -> set[str]:
        """
        Si `entity` tombe, quelles autres entités sont potentiellement
        affectées en aval (critère 3 : propagation de l'impact) ?
        Parcours en largeur sur l'index inverse (dépendants directs et
        indirects), sans jamais boucler à l'infini en cas de cycle.
        """
        affected = set()
        to_visit = list(self._dependents.get(entity, []))
        while to_visit:
            current = to_visit.pop()
            if current in affected:
                continue
            affected.add(current)
            to_visit.extend(self._dependents.get(current, []))
        return affected

    def has_upstream_within(self, entity: str, candidate_set: set[str]) -> bool:
        """
        `entity` a-t-elle une dépendance directe qui fait aussi partie de
        `candidate_set` ? Utilisé par le moteur RCA pour éliminer les
        entités qui ne sont pas "la plus en amont" du groupe d'alertes.
        """
        return any(dep in candidate_set for dep in self.upstream_dependencies(entity))
