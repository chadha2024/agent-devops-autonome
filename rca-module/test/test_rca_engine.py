from datetime import datetime, timedelta, timezone
import pytest

from alert_model import Alert
from alert_correlator import IncidentGroup
from service_graph import ServiceGraph
from rca_engine import RCAEngine


def make_alert(entity, minutes_offset):
    base = datetime(2026, 8, 11, 12, 0, 0, tzinfo=timezone.utc)
    return Alert(
        timestamp=base + timedelta(minutes=minutes_offset),
        source="epic1_metrics",
        entity=entity,
        severity="critical",
        detail="test",
        raw={},
    )


@pytest.fixture
def graph():
    return ServiceGraph(path="service_graph.yaml")


@pytest.fixture
def engine(graph):
    return RCAEngine(graph)


class TestRCAEngine:
    def test_cascade_complete_identifie_la_vraie_racine(self, engine):
        """
        Scénario : eks-node-group tombe en premier, ce qui fait tomber
        prometheus, ce qui fait échouer anomaly-detector en cascade.
        La cause racine doit être eks-node-group, pas les symptômes en aval.
        """
        group = IncidentGroup(alerts=[
            make_alert("eks-node-group", 0),
            make_alert("prometheus", 1),
            make_alert("anomaly-detector", 2),
        ])
        result = engine.analyze(group)

        assert result.root_cause_entity == "eks-node-group"
        assert result.root_cause_type == "infra"
        assert result.confidence_percent == 100  # les 3 entités sont expliquées

    def test_une_seule_entite_en_alerte(self, engine):
        group = IncidentGroup(alerts=[make_alert("rds-postgres", 0)])
        result = engine.analyze(group)

        assert result.root_cause_entity == "rds-postgres"
        assert result.confidence_percent == 100

    def test_incident_partiel_confiance_reduite(self, engine):
        """
        Deux entités en alerte qui n'ont AUCUN lien de dépendance entre
        elles (incidents simultanés mais indépendants) -> la confiance doit
        refléter que l'une des deux n'est pas expliquée par l'autre.
        """
        group = IncidentGroup(alerts=[
            make_alert("gemini-api", 0),      # aucune dépendance avec sqs-queue
            make_alert("sqs-queue", 0),
        ])
        result = engine.analyze(group)

        assert result.confidence_percent < 100
        assert len(result.unexplained_entities) >= 1

    def test_type_categorise_correctement_service_infra_network(self, engine):
        group = IncidentGroup(alerts=[make_alert("gemini-api", 0)])
        result = engine.analyze(group)
        assert result.root_cause_type == "network"

        group2 = IncidentGroup(alerts=[make_alert("anomaly-detector", 0)])
        result2 = engine.analyze(group2)
        assert result2.root_cause_type == "service"

    def test_to_dict_est_json_serialisable(self, engine):
        import json
        group = IncidentGroup(alerts=[
            make_alert("eks-node-group", 0),
            make_alert("prometheus", 1),
        ])
        result = engine.analyze(group)
        json.dumps(result.to_dict())  # ne doit lever aucune exception

    def test_deux_racines_independantes_dans_le_meme_groupe(self, engine):
        """
        Si deux pannes vraiment indépendantes sont corrélées par erreur
        (même fenêtre de temps, mais aucun lien de dépendance), le moteur
        doit choisir UNE racine (comme demandé par le critère) tout en
        signalant explicitement ce qui reste inexpliqué, plutôt que
        prétendre à tort que tout est cohérent.
        """
        group = IncidentGroup(alerts=[
            make_alert("gemini-api", 0),
            make_alert("sqs-queue", 5),
        ])
        result = engine.analyze(group)

        assert result.root_cause_entity in {"gemini-api", "sqs-queue"}
        assert "gemini-api" in result.unexplained_entities or "sqs-queue" in result.unexplained_entities
