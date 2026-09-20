"""
Corrélation multi-métriques.

Le but : ne pas déclencher une alerte "critique" sur la seule base d'une
métrique isolée (ex: CPU haute toute seule = peut-être juste un pic de
charge normal). On regroupe plusieurs signaux, PAR INSTANCE/POD, pour
distinguer un vrai incident d'un bruit ponctuel.
"""

from dataclasses import dataclass, field


@dataclass
class MetricSignal:
    """Le résultat de détection pour une seule métrique, sur une seule instance/pod."""
    name: str
    instance: str                        # US 1.2, critère 3 : identifie le pod/nœud concerné
    job: str = "unknown"                 # complète l'identification du "service"
    is_anomaly: bool = False             # anomalie statistique (z-score)
    zscore: float | None = None
    is_degrading: bool = False           # dégradation progressive (pente)
    slope_per_minute: float = 0.0
    absolute_breach: bool = False        # seuil critique absolu franchi (US 1.2, critère 1)
    absolute_threshold: float = 0.0
    current_value: float = 0.0

    @property
    def triggered(self) -> bool:
        return self.is_anomaly or self.is_degrading or self.absolute_breach


@dataclass
class CorrelatedIncident:
    """Le résultat final après corrélation de plusieurs métriques SUR LA MÊME INSTANCE."""
    instance: str
    job: str
    signals: list[MetricSignal] = field(default_factory=list)

    @property
    def anomaly_count(self) -> int:
        return sum(1 for s in self.signals if s.triggered)

    @property
    def severity(self) -> str:
        """
        Règle de corrélation :
        - 0 métrique anormale sur cette instance  -> pas d'incident
        - 1 métrique anormale                     -> warning
        - 2+ métriques anormales en même temps    -> critical
        - Un dépassement de seuil ABSOLU est toujours au moins "critical",
          même seul, car c'est un garde-fou volontairement strict (US 1.2).
        """
        if any(s.absolute_breach for s in self.signals):
            return "critical"
        if self.anomaly_count == 0:
            return "none"
        elif self.anomaly_count == 1:
            return "warning"
        else:
            return "critical"

    @property
    def is_incident(self) -> bool:
        return self.anomaly_count > 0

    def summary(self) -> str:
        parts = []
        for s in self.signals:
            if s.absolute_breach:
                parts.append(f"{s.name}=seuil_absolu_dépassé(valeur={s.current_value:.2f}>{s.absolute_threshold})")
            if s.is_anomaly:
                parts.append(f"{s.name}=anomalie(z={s.zscore:.2f})")
            if s.is_degrading:
                parts.append(f"{s.name}=dégradation({s.slope_per_minute:.2f}%/min)")
        return ", ".join(parts) if parts else "aucune anomalie"


def correlate_by_instance(signals: list[MetricSignal]) -> list[CorrelatedIncident]:
    """
    Regroupe les signaux par instance/pod, puis corrèle les métriques
    de chaque groupe séparément. Une instance saine ne doit jamais être
    masquée ou mélangée avec une instance en incident.
    """
    grouped: dict[str, list[MetricSignal]] = {}
    jobs: dict[str, str] = {}
    for s in signals:
        grouped.setdefault(s.instance, []).append(s)
        jobs[s.instance] = s.job

    return [
        CorrelatedIncident(instance=instance, job=jobs[instance], signals=sigs)
        for instance, sigs in grouped.items()
    ]