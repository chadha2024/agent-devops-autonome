"""
Moteur de décision (Ticket "Moteur de décision avec évaluation des risques").

Prend les remédiations éligibles (knowledge_base.py) et construit un plan
d'exécution séquencé : quelle action tenter en premier, puis en cascade si
elle échoue, avec une évaluation du risque global et une décision
préliminaire d'approbation automatique ou humaine.

Stratégie de sélection (validée avec l'encadrant) :
  - Par défaut : RISQUE D'ABORD — l'action la moins risquée est tentée en
    premier, l'efficacité historique ne sert qu'à départager les égalités.
  - Exception : priorité P1 — EFFICACITÉ D'ABORD. Une urgence critique
    justifie de tenter l'action la plus fiable en premier, même si un peu
    plus risquée, plutôt que de perdre du temps sur l'option la plus douce.
"""

from dataclasses import dataclass, field

from knowledge_base import KnowledgeBase, RemediationOption, RISK_ORDER

P1_PRIORITY = "P1"


@dataclass
class ExecutionStep:
    step_number: int
    action_id: str
    description: str
    risk_level: str
    downtime_seconds: float
    effectiveness_rate: float | None
    attempts_count: int

    def to_dict(self) -> dict:
        return {
            "step_number": self.step_number,
            "action_id": self.action_id,
            "description": self.description,
            "risk_level": self.risk_level,
            "downtime_seconds": self.downtime_seconds,
            "effectiveness_rate": self.effectiveness_rate,
            "attempts_count": self.attempts_count,
        }


@dataclass
class ExecutionPlan:
    entity: str
    error_category: str
    priority: str
    selection_strategy: str          # "risk_first" ou "efficacy_first"
    steps: list[ExecutionStep] = field(default_factory=list)
    requires_approval: bool = True
    worst_case_downtime_seconds: float = 0

    @property
    def primary_step(self) -> ExecutionStep | None:
        return self.steps[0] if self.steps else None

    def to_dict(self) -> dict:
        return {
            "entity": self.entity,
            "error_category": self.error_category,
            "priority": self.priority,
            "selection_strategy": self.selection_strategy,
            "steps": [s.to_dict() for s in self.steps],
            "requires_approval": self.requires_approval,
            "worst_case_downtime_seconds": self.worst_case_downtime_seconds,
        }


class DecisionEngine:
    def __init__(self, kb: KnowledgeBase):
        self.kb = kb

    def build_plan(self, entity: str, error_category: str, priority: str,
                    context: dict | None = None) -> ExecutionPlan:
        options = self.kb.eligible_remediations(error_category, entity, context)
        strategy = "efficacy_first" if priority == P1_PRIORITY else "risk_first"

        ordered = self._order_by_strategy(options, strategy)

        steps = [
            ExecutionStep(
                step_number=i + 1,
                action_id=o.action_id,
                description=o.description,
                risk_level=o.risk_level,
                downtime_seconds=o.downtime_seconds,
                effectiveness_rate=o.effectiveness_rate,
                attempts_count=o.attempts_count,
            )
            for i, o in enumerate(ordered)
        ]

        plan = ExecutionPlan(
            entity=entity,
            error_category=error_category,
            priority=priority,
            selection_strategy=strategy,
            steps=steps,
            worst_case_downtime_seconds=sum(s.downtime_seconds for s in steps),
        )
        plan.requires_approval = self._needs_human_approval(plan.primary_step)
        return plan

    @staticmethod
    def _order_by_strategy(options: list[RemediationOption], strategy: str) -> list[RemediationOption]:
        """
        IMPORTANT : le risque "none" ne veut PAS dire "aucun risque, donc
        à essayer en premier" — il veut dire "c'est un humain qui agit,
        pas l'agent". Une action humaine (escalate_human) doit donc
        toujours passer EN DERNIER dans la cascade, après avoir épuisé
        les options automatisables — sinon l'agent appellerait un humain
        avant même d'avoir tenté l'action automatique la plus sûre.
        """
        if not options:
            return []

        automatable = [o for o in options if o.risk_level != "none"]
        human_only = [o for o in options if o.risk_level == "none"]

        if strategy == "efficacy_first":
            def key(o: RemediationOption):
                return (
                    0 if o.effectiveness_rate is not None else 1,
                    -(o.effectiveness_rate or 0),
                    RISK_ORDER.get(o.risk_level, 2),
                )
        else:  # risk_first (par défaut)
            def key(o: RemediationOption):
                return (
                    RISK_ORDER.get(o.risk_level, 2),
                    0 if o.effectiveness_rate is not None else 1,
                    -(o.effectiveness_rate or 0),
                )

        return sorted(automatable, key=key) + human_only

    @staticmethod
    def _needs_human_approval(primary: ExecutionStep | None) -> bool:
        """
        Décision préliminaire (le Ticket 5, dédié, affinera les règles
        précises). Ici : tout ce qui n'est pas explicitement "low" attend
        une validation humaine — y compris "none" (= action déjà humaine
        par nature, ex: escalate_human) et "medium"/"high" (risque réel).
        """
        if primary is None:
            return True  # aucune remédiation disponible -> forcément humain
        return primary.risk_level != "low"
