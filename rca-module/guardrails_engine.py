"""
Garde-fous globaux (Ticket "Guardrails et blast radius control").

Dernier filet de sécurité avant exécution réelle — vérifié APRÈS que
decision_engine et approval_engine aient déjà validé une action, jamais
avant. Trois contrôles indépendants, chacun suffisant à lui seul pour
bloquer l'exécution :
  1. Liste blanche : l'action est-elle explicitement autorisée en auto ?
  2. Limites de scope : l'ampleur de l'action reste-t-elle raisonnable ?
  3. Kill switch : un humain a-t-il coupé toute automatisation ?
"""

from dataclasses import dataclass, field

import yaml

from logging_setup import get_logger

logger = get_logger(__name__)


@dataclass
class GuardrailCheck:
    allowed: bool
    reasons: list[str] = field(default_factory=list)  # motifs de blocage, vide si autorisé

    def to_dict(self) -> dict:
        return {"allowed": self.allowed, "reasons": self.reasons}


class GuardrailsEngine:
    def __init__(self, policy_path: str = "guardrails_policy.yaml"):
        self.policy_path = policy_path
        self._load_policy()

    def _load_policy(self) -> None:
        """Recharge la politique à chaque vérification (pas mise en cache
        durablement) : le kill switch doit pouvoir être activé par un
        humain à tout moment et être pris en compte immédiatement, sans
        redémarrer le processus de l'agent."""
        with open(self.policy_path, "r", encoding="utf-8") as f:
            self.policy = yaml.safe_load(f)

    def is_kill_switch_active(self) -> bool:
        self._load_policy()
        return bool(self.policy.get("kill_switch", {}).get("enabled", False))

    def check(self, action_id: str, pods_affected: int = 1, scale_factor: float = 1.0,
              risk_level: str | None = None) -> GuardrailCheck:
        """
        Vérifie les 3 garde-fous pour une action donnée. Retourne TOUS les
        motifs de blocage en même temps (pas juste le premier trouvé), pour
        que l'humain qui lit le résultat comprenne tout d'un coup plutôt
        que de devoir relancer plusieurs fois pour découvrir les problèmes
        un par un.

        Exception : une action de risque "none" (ex: escalate_human) n'est
        PAS une action automatique — c'est justement la demande d'une
        intervention humaine. Elle ne doit jamais être bloquée par la
        liste blanche (faite pour limiter ce que la MACHINE peut faire
        seule) ni par le kill switch (qui coupe l'automatisation, pas la
        possibilité de prévenir un humain — au contraire, on en a
        d'autant plus besoin en cas d'urgence).
        """
        if risk_level == "none":
            return GuardrailCheck(allowed=True, reasons=[])

        self._load_policy()
        reasons = []

        if self.policy.get("kill_switch", {}).get("enabled", False):
            reasons.append("kill switch d'urgence activé : toute exécution automatique est bloquée")

        allowed_actions = self.policy.get("allowed_actions", [])
        if action_id not in allowed_actions:
            reasons.append(f"'{action_id}' n'est pas dans la liste blanche des actions autorisées en automatique")

        limits = self.policy.get("scope_limits", {})
        max_pods = limits.get("max_pods_affected")
        if max_pods is not None and pods_affected > max_pods:
            reasons.append(f"{pods_affected} pods affectés > limite autorisée ({max_pods})")

        max_scale = limits.get("max_scale_factor")
        if max_scale is not None and scale_factor > max_scale:
            reasons.append(f"facteur d'échelle {scale_factor}x > limite autorisée ({max_scale}x)")

        check = GuardrailCheck(allowed=len(reasons) == 0, reasons=reasons)
        if not check.allowed:
            logger.warning("Action '%s' bloquée par les guardrails : %s", action_id, "; ".join(reasons))
        return check