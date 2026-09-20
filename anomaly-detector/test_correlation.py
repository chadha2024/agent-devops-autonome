"""
Tests unitaires pour correlation.py — corrélation multi-métriques
par instance/pod.
"""

from correlation import MetricSignal, CorrelatedIncident, correlate_by_instance


class TestMetricSignalTriggered:

    def test_no_anomaly_is_not_triggered(self):
        s = MetricSignal(name="cpu_percent", instance="node-1")
        assert s.triggered is False

    def test_statistical_anomaly_is_triggered(self):
        s = MetricSignal(name="cpu_percent", instance="node-1", is_anomaly=True)
        assert s.triggered is True

    def test_absolute_breach_is_triggered(self):
        s = MetricSignal(name="cpu_percent", instance="node-1", absolute_breach=True)
        assert s.triggered is True


class TestCorrelatedIncidentSeverity:

    def test_no_signal_triggered_means_no_incident(self):
        incident = CorrelatedIncident(
            instance="node-1", job="k8s",
            signals=[MetricSignal(name="cpu_percent", instance="node-1")],
        )
        assert incident.severity == "none"
        assert incident.is_incident is False

    def test_single_anomaly_is_warning(self):
        incident = CorrelatedIncident(
            instance="node-1", job="k8s",
            signals=[MetricSignal(name="cpu_percent", instance="node-1", is_anomaly=True)],
        )
        assert incident.severity == "warning"

    def test_two_anomalies_is_critical(self):
        signals = [
            MetricSignal(name="cpu_percent", instance="node-1", is_anomaly=True),
            MetricSignal(name="ram_percent", instance="node-1", is_degrading=True),
        ]
        incident = CorrelatedIncident(instance="node-1", job="k8s", signals=signals)
        assert incident.severity == "critical"

    def test_absolute_breach_alone_is_always_critical(self):
        # Garde-fou volontairement strict : un seul dépassement de seuil
        # absolu suffit, même sans corrélation avec une autre métrique.
        incident = CorrelatedIncident(
            instance="node-1", job="k8s",
            signals=[MetricSignal(
                name="cpu_percent", instance="node-1",
                absolute_breach=True, current_value=96.0, absolute_threshold=90,
            )],
        )
        assert incident.severity == "critical"


class TestCorrelateByInstance:

    def test_signals_are_grouped_by_instance(self):
        signals = [
            MetricSignal(name="cpu_percent", instance="node-1", job="k8s", is_anomaly=True),
            MetricSignal(name="ram_percent", instance="node-1", job="k8s"),
            MetricSignal(name="cpu_percent", instance="node-2", job="k8s"),
        ]
        incidents = correlate_by_instance(signals)
        instances = {i.instance for i in incidents}

        assert instances == {"node-1", "node-2"}

    def test_incident_on_one_instance_does_not_mask_another(self):
        # Une instance en incident critique ne doit jamais influencer
        # la sévérité calculée pour une autre instance saine.
        signals = [
            MetricSignal(name="cpu_percent", instance="node-1", job="k8s", is_anomaly=True),
            MetricSignal(name="ram_percent", instance="node-1", job="k8s", is_degrading=True),
            MetricSignal(name="cpu_percent", instance="node-2", job="k8s"),
        ]
        incidents = correlate_by_instance(signals)
        by_instance = {i.instance: i for i in incidents}

        assert by_instance["node-1"].severity == "critical"
        assert by_instance["node-2"].severity == "none"