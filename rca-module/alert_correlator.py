"""
Corrélation temporelle des alertes (Ticket RCA, critère 1).

Regroupe les alertes qui arrivent dans une fenêtre de temps proche en un
seul "groupe d'incident", plutôt que de les traiter comme N alertes
indépendantes. Sans ça, un incident qui touche 3 services en cascade
génère 3 diagnostics séparés, sans lien évident entre eux.
"""

from dataclasses import dataclass, field
from alert_model import Alert

DEFAULT_CORRELATION_WINDOW_SECONDS = 300  # 5 minutes


@dataclass
class IncidentGroup:
    """Un ensemble d'alertes considérées comme faisant partie du même incident."""
    alerts: list[Alert] = field(default_factory=list)

    @property
    def entities(self) -> set[str]:
        return {a.entity for a in self.alerts}

    @property
    def start_time(self):
        return min(a.timestamp for a in self.alerts)

    @property
    def end_time(self):
        return max(a.timestamp for a in self.alerts)


class TemporalCorrelator:
    def __init__(self, window_seconds: int = DEFAULT_CORRELATION_WINDOW_SECONDS):
        self.window_seconds = window_seconds

    def correlate(self, alerts: list[Alert]) -> list[IncidentGroup]:
        """
        Regroupe une liste d'alertes en groupes d'incidents.

        Algorithme glouton chronologique : on trie les alertes par temps,
        puis on étend un groupe tant que la prochaine alerte arrive dans
        `window_seconds` de la DERNIÈRE alerte du groupe courant (fenêtre
        glissante, pas fixe — un incident qui traîne reste un seul groupe
        tant que les alertes s'enchaînent sans trou de silence).
        """
        if not alerts:
            return []

        sorted_alerts = sorted(alerts, key=lambda a: a.timestamp)
        groups: list[IncidentGroup] = [IncidentGroup(alerts=[sorted_alerts[0]])]

        for alert in sorted_alerts[1:]:
            current_group = groups[-1]
            gap = (alert.timestamp - current_group.end_time).total_seconds()
            if gap <= self.window_seconds:
                current_group.alerts.append(alert)
            else:
                groups.append(IncidentGroup(alerts=[alert]))

        return groups
