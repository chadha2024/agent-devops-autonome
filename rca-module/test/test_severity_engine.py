from datetime import datetime, timedelta, timezone
import pytest

from alert_model import Alert
from alert_correlator import IncidentGroup
from rca_engine import RCAResult
from severity_engine import SeverityClassifier, prioritize


def make_rca_result(entities, root_cause, start_offset_minutes):
    """Construit un RCAResult minimal pour tester le scoring de sévérité
    isolément, sans dépendre du vrai moteur RCA."""
    base = datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc)
    alerts = [
        Alert(timestamp=base, source="epic1_metrics", entity=e, severity="critical", detail="", raw={})
        for e in entities
    ]
    group = IncidentGroup(alerts=alerts)
    return RCAResult(
        incident_group=group,
        root_cause_entity=root_cause,
        root_cause_type="infra",
        confidence_percent=100,
        correlated_alert_count=len(alerts),
        explained_entities=set(entities),
        unexplained_entities=set(),
    )


@pytest.fixture
def classifier():
    return SeverityClassifier(criticality_path="business_criticality.yaml")


class TestSeverityScoring:
    def test_incident_critique_cascade_devient_p1(self, classifier):
        """Reprend l'exemple travaillé ensemble : eks-node-group, 3 entités
        touchées, incident vieux de 20 min (SLA cible 15 min) -> P1."""
        result = make_rca_result(
            entities=["eks-node-group", "prometheus", "anomaly-detector"],
            root_cause="eks-node-group",
            start_offset_minutes=20,
        )
        now = datetime(2026, 8, 14, 12, 20, 0, tzinfo=timezone.utc)  # +20 min
        severity = classifier.classify(result, now=now)

        assert severity.priority == "P1"
        assert severity.business_tier == "critical"
        assert severity.blast_radius == 3
        assert severity.sla_breach_risk is True
        assert severity.sla_minutes_remaining < 0  # déjà en retard

    def test_incident_mineur_isole_devient_p4(self, classifier):
        """crash-test, 1 seule entité, incident très récent -> P4."""
        result = make_rca_result(
            entities=["crash-test"], root_cause="crash-test", start_offset_minutes=5,
        )
        now = datetime(2026, 8, 14, 12, 5, 0, tzinfo=timezone.utc)
        severity = classifier.classify(result, now=now)

        assert severity.priority == "P4"
        assert severity.business_tier == "low"
        assert severity.sla_breach_risk is False

    def test_entite_inconnue_utilise_le_defaut(self, classifier):
        """Une entité absente de business_criticality.yaml ne doit jamais
        planter — elle retombe sur le palier 'default' (medium)."""
        result = make_rca_result(
            entities=["service-jamais-vu"], root_cause="service-jamais-vu", start_offset_minutes=0,
        )
        now = datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc)
        severity = classifier.classify(result, now=now)

        assert severity.business_tier == "medium"
        assert severity.priority in {"P1", "P2", "P3", "P4"}  # ne plante pas

    def test_sla_breach_risk_declenche_a_80_pourcent(self, classifier):
        """Le risque SLA doit se déclencher AVANT le dépassement réel,
        dès 80% du délai écoulé (critère 3 : prédiction, pas constat)."""
        # rds-postgres : sla_minutes = 15. 80% de 15 = 12 min.
        result = make_rca_result(
            entities=["rds-postgres"], root_cause="rds-postgres", start_offset_minutes=12,
        )
        now_juste_avant = datetime(2026, 8, 14, 12, 11, 0, tzinfo=timezone.utc)  # 11 min < 12
        now_juste_apres = datetime(2026, 8, 14, 12, 13, 0, tzinfo=timezone.utc)  # 13 min > 12

        s1 = classifier.classify(result, now=now_juste_avant)
        s2 = classifier.classify(result, now=now_juste_apres)

        assert s1.sla_breach_risk is False
        assert s2.sla_breach_risk is True
        assert s2.sla_minutes_remaining > 0  # pas encore dépassé, juste à risque

    def test_blast_radius_plafonne_a_100(self, classifier):
        """Beaucoup d'entités touchées ne doit pas faire dépasser le score max."""
        result = make_rca_result(
            entities=[f"entity-{i}" for i in range(10)], root_cause="entity-0", start_offset_minutes=0,
        )
        now = datetime(2026, 8, 14, 12, 0, 0, tzinfo=timezone.utc)
        severity = classifier.classify(result, now=now)
        assert severity.severity_score <= 100


class TestPrioritize:
    def test_incident_critique_passe_avant_incident_mineur(self, classifier):
        incident_a = make_rca_result(
            entities=["eks-node-group", "prometheus", "anomaly-detector"],
            root_cause="eks-node-group", start_offset_minutes=20,
        )
        incident_b = make_rca_result(entities=["crash-test"], root_cause="crash-test", start_offset_minutes=5)

        now = datetime(2026, 8, 14, 12, 20, 0, tzinfo=timezone.utc)
        sa = classifier.classify(incident_a, now=now)
        sb = classifier.classify(incident_b, now=now)

        ordered = prioritize([sb, sa])  # volontairement dans le mauvais ordre au départ
        assert ordered[0].entity == "eks-node-group"
        assert ordered[1].entity == "crash-test"

    def test_egalite_de_score_departagee_par_marge_sla(self, classifier):
        """À score identique, celui qui a le moins de marge SLA restante
        doit passer en premier."""
        r1 = make_rca_result(entities=["rds-postgres"], root_cause="rds-postgres", start_offset_minutes=10)
        r2 = make_rca_result(entities=["rds-postgres"], root_cause="rds-postgres", start_offset_minutes=10)

        now = datetime(2026, 8, 14, 12, 10, 0, tzinfo=timezone.utc)
        s1 = classifier.classify(r1, now=now)
        s2 = classifier.classify(r2, now=now + timedelta(minutes=3))  # plus proche du SLA

        ordered = prioritize([s1, s2])
        assert ordered[0].sla_minutes_remaining <= ordered[1].sla_minutes_remaining

    def test_liste_vide(self):
        assert prioritize([]) == []
