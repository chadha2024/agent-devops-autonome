from datetime import datetime, timedelta, timezone
import pytest

from alert_model import Alert
from alert_correlator import TemporalCorrelator


def make_alert(entity, minutes_offset, severity="critical"):
    base = datetime(2026, 8, 11, 12, 0, 0, tzinfo=timezone.utc)
    return Alert(
        timestamp=base + timedelta(minutes=minutes_offset),
        source="epic1_metrics",
        entity=entity,
        severity=severity,
        detail="test",
        raw={},
    )


class TestTemporalCorrelator:
    def test_liste_vide(self):
        correlator = TemporalCorrelator(window_seconds=300)
        assert correlator.correlate([]) == []

    def test_une_seule_alerte_un_seul_groupe(self):
        correlator = TemporalCorrelator(window_seconds=300)
        groups = correlator.correlate([make_alert("eks-node-group", 0)])
        assert len(groups) == 1
        assert len(groups[0].alerts) == 1

    def test_alertes_proches_dans_le_meme_groupe(self):
        """3 alertes en cascade dans la même fenêtre de 5 min -> 1 seul incident."""
        alerts = [
            make_alert("eks-node-group", 0),
            make_alert("prometheus", 1),
            make_alert("anomaly-detector", 3),
        ]
        correlator = TemporalCorrelator(window_seconds=300)
        groups = correlator.correlate(alerts)
        assert len(groups) == 1
        assert len(groups[0].alerts) == 3

    def test_alertes_eloignees_dans_des_groupes_separes(self):
        """Deux alertes séparées de 20 minutes (fenêtre 5 min) -> 2 incidents distincts."""
        alerts = [
            make_alert("eks-node-group", 0),
            make_alert("rds-postgres", 20),
        ]
        correlator = TemporalCorrelator(window_seconds=300)
        groups = correlator.correlate(alerts)
        assert len(groups) == 2

    def test_fenetre_glissante_pas_fixe(self):
        """Un incident qui s'étale sur 12 min via des alertes tous les 4 min
        reste un seul groupe (chaque écart < fenêtre), même si l'écart total
        entre la 1ère et la dernière dépasse la fenêtre."""
        alerts = [
            make_alert("eks-node-group", 0),
            make_alert("prometheus", 4),
            make_alert("loki", 8),
            make_alert("anomaly-detector", 12),
        ]
        correlator = TemporalCorrelator(window_seconds=300)  # 5 min
        groups = correlator.correlate(alerts)
        assert len(groups) == 1
        assert len(groups[0].alerts) == 4

    def test_ordre_d_entree_n_importe_pas(self):
        """Les alertes désordonnées doivent quand même être triées avant corrélation."""
        alerts = [
            make_alert("anomaly-detector", 3),
            make_alert("eks-node-group", 0),
            make_alert("prometheus", 1),
        ]
        correlator = TemporalCorrelator(window_seconds=300)
        groups = correlator.correlate(alerts)
        assert len(groups) == 1
        assert groups[0].start_time == make_alert("eks-node-group", 0).timestamp
