import pytest

from decision_engine import ExecutionPlan, ExecutionStep
from approval_engine import ApprovalManager, ApprovalStatus, NotificationChannel


class RecordingChannel(NotificationChannel):
    """Canal de test qui garde en mémoire tout ce qui a été notifié,
    pour vérifier QUI a été notifié et QUAND, sans dépendre d'un vrai
    Slack/email."""
    def __init__(self):
        self.sent = []

    def notify(self, role: str, message: str) -> None:
        self.sent.append((role, message))


def make_plan(risk_level: str, priority: str = "P3", entity: str = "x") -> ExecutionPlan:
    step = ExecutionStep(
        step_number=1, action_id="some_action", description="desc",
        risk_level=risk_level, downtime_seconds=10,
        effectiveness_rate=None, attempts_count=0,
    )
    return ExecutionPlan(
        entity=entity, error_category="OutOfMemory", priority=priority,
        selection_strategy="risk_first", steps=[step],
    )


@pytest.fixture
def channel():
    return RecordingChannel()


@pytest.fixture
def manager(tmp_path, channel):
    return ApprovalManager(
        policy_path="approval_policy.yaml",
        notification_channel=channel,
        audit_log_path=str(tmp_path / "audit.jsonl"),
    )


class TestAutoApprove:
    def test_risque_faible_auto_approuve_sans_notification(self, manager, channel):
        plan = make_plan(risk_level="low")
        request = manager.request_approval(plan)

        assert request.status == ApprovalStatus.AUTO_APPROVED
        assert channel.sent == []  # personne n'est dérangé pour du low-risk


class TestNotificationEtAttente:
    def test_risque_moyen_notifie_l_astreinte(self, manager, channel):
        plan = make_plan(risk_level="medium")
        request = manager.request_approval(plan)

        assert request.status == ApprovalStatus.PENDING
        assert request.approver_role == "on-call-engineer"
        assert len(channel.sent) == 1
        assert channel.sent[0][0] == "on-call-engineer"

    def test_risque_eleve_notifie_le_tech_lead(self, manager, channel):
        plan = make_plan(risk_level="high")
        request = manager.request_approval(plan)

        assert request.approver_role == "tech-lead"

    def test_timeout_depend_de_la_priorite(self, manager):
        plan_p1 = make_plan(risk_level="medium", priority="P1")
        plan_p4 = make_plan(risk_level="medium", priority="P4")

        r1 = manager.request_approval(plan_p1, now=1000)
        r4 = manager.request_approval(plan_p4, now=1000)

        assert (r1.timeout_at - 1000) < (r4.timeout_at - 1000)  # P1 attend moins longtemps


class TestTimeoutEtEscalade:
    def test_pas_encore_timeout_reste_pending(self, manager):
        plan = make_plan(risk_level="medium", priority="P2")  # timeout 30 min
        request = manager.request_approval(plan, now=1000)

        checked = manager.check_timeout(request, now=1000 + 60)  # 1 min plus tard seulement
        assert checked.status == ApprovalStatus.PENDING
        assert checked.escalation_level == 0

    def test_premier_timeout_escalade(self, manager, channel):
        plan = make_plan(risk_level="medium", priority="P2")  # timeout 30 min
        request = manager.request_approval(plan, now=1000)

        checked = manager.check_timeout(request, now=1000 + 31 * 60)  # 31 min plus tard
        assert checked.escalation_level == 1
        assert checked.approver_role == "tech-lead"
        assert checked.status == ApprovalStatus.PENDING  # de nouveau en attente, au niveau escaladé
        assert any(role == "tech-lead" for role, _ in channel.sent)

    def test_deuxieme_timeout_declenche_alerte_critique(self, manager, channel):
        plan = make_plan(risk_level="medium", priority="P2")
        request = manager.request_approval(plan, now=1000)

        # 1er timeout -> escalade
        request = manager.check_timeout(request, now=1000 + 31 * 60)
        # 2ème timeout (sur le délai d'escalade, plus court) -> alerte critique
        request = manager.check_timeout(request, now=request.timeout_at + 1)

        assert request.status == ApprovalStatus.ESCALATION_EXHAUSTED
        assert any(role == "critical-alert" for role, _ in channel.sent)

    def test_jamais_bloque_silencieusement(self, manager):
        """Peu importe combien de fois on vérifie après le double timeout,
        le statut reste explicite (ESCALATION_EXHAUSTED), jamais un vide
        ou une exception qui masquerait le problème."""
        plan = make_plan(risk_level="high", priority="P1")
        request = manager.request_approval(plan, now=1000)
        request = manager.check_timeout(request, now=request.timeout_at + 1)
        request = manager.check_timeout(request, now=request.timeout_at + 1)

        assert request.status == ApprovalStatus.ESCALATION_EXHAUSTED


class TestDecision:
    def test_approbation_humaine_enregistree(self, manager):
        plan = make_plan(risk_level="medium")
        request = manager.request_approval(plan)
        decided = manager.decide(request, approved=True, decided_by="alice")

        assert decided.status == ApprovalStatus.APPROVED
        assert decided.decided_by == "alice"

    def test_rejet_humain_enregistre(self, manager):
        plan = make_plan(risk_level="medium")
        request = manager.request_approval(plan)
        decided = manager.decide(request, approved=False, decided_by="bob")

        assert decided.status == ApprovalStatus.REJECTED


class TestAuditLog:
    def test_decisions_finales_ecrites_dans_le_log(self, manager, tmp_path):
        plan = make_plan(risk_level="low")
        manager.request_approval(plan)  # auto-approuvé -> doit être audité

        audit_file = tmp_path / "audit.jsonl"
        assert audit_file.exists()
        content = audit_file.read_text()
        assert "auto_approved" in content
