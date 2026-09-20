import pytest
from service_graph import ServiceGraph


@pytest.fixture
def graph():
    return ServiceGraph(path="service_graph.yaml")


class TestServiceGraph:
    def test_charge_les_entites(self, graph):
        assert "anomaly-detector" in graph.entities
        assert "eks-node-group" in graph.entities

    def test_entity_type(self, graph):
        assert graph.entity_type("anomaly-detector") == "service"
        assert graph.entity_type("rds-postgres") == "infra"
        assert graph.entity_type("gemini-api") == "network"

    def test_entity_type_inconnue(self, graph):
        assert graph.entity_type("service-qui-n-existe-pas") == "unknown"

    def test_resolve_from_node(self, graph):
    # ⚠️ Limite connue : sur un cluster à un seul nœud (minikube), plusieurs
    # entités partagent la même adresse "hosted_on" — resolve_from_node()
    # ne peut renvoyer qu'UNE seule d'entre elles (la dernière lue dans le
    # YAML), pas un vrai choix fiable. Sur un vrai cluster multi-nœuds,
    # chaque service aurait sa propre adresse et ce problème n'existerait pas.
        result = graph.resolve_from_node("192.168.49.2:9100")
        assert result in {"anomaly-detector", "llm-log-analyzer", "crash-test"}
        assert graph.resolve_from_node("noeud-inconnu") is None
    def test_upstream_dependencies(self, graph):
        deps = graph.upstream_dependencies("llm-log-analyzer")
        assert set(deps) == {"loki", "gemini-api", "sqs-queue"}

    def test_upstream_dependencies_entite_sans_dependance(self, graph):
        assert graph.upstream_dependencies("eks-node-group") == []

    def test_propagate_impact_direct(self, graph):
        # Si eks-node-group tombe, prometheus/loki/rds-postgres en dépendent directement
        affected = graph.propagate_impact("eks-node-group")
        assert "prometheus" in affected
        assert "loki" in affected
        assert "rds-postgres" in affected

    def test_propagate_impact_transitif(self, graph):
        # eks-node-group -> prometheus -> anomaly-detector (dépendance indirecte)
        affected = graph.propagate_impact("eks-node-group")
        assert "anomaly-detector" in affected  # dépend de prometheus, qui dépend de eks-node-group

    def test_propagate_impact_feuille_sans_dependant(self, graph):
        # anomaly-detector n'a personne qui dépend de lui
        affected = graph.propagate_impact("anomaly-detector")
        assert affected == set()

    def test_has_upstream_within(self, graph):
        candidate_set = {"llm-log-analyzer", "loki", "gemini-api"}
        assert graph.has_upstream_within("llm-log-analyzer", candidate_set) is True
        assert graph.has_upstream_within("loki", candidate_set) is False  # loki dépend de eks-node-group, absent du set
