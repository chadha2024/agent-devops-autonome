"""
Audit trail et reporting (US 4.1 du cahier des charges + Ticket Jira
"Configurer l'audit trail et le reporting des actions").

Ne collecte AUCUNE nouvelle donnée — tout existe déjà, écrit par les
modules précédents :
  - incident_log.jsonl         (live_demo.py)       : UN incident = UNE ligne complète
  - remediation_history.jsonl  (knowledge_base.py)  : chaque tentative d'action
  - approval_audit.jsonl       (approval_engine.py) : chaque décision d'approbation

Respecte les 2 premiers critères de l'US 4.1 :
  1. Nombre total d'incidents détectés / résolus automatiquement / escaladés
  2. Liste détaillée avec horodatage, service concerné, action entreprise
(Le 3e critère — interface web sécurisée — est géré par live_server.py.)
"""

import json
import os
import statistics
from dataclasses import dataclass, field

MTTR_UNAVAILABLE_LABEL = "N/A (pas assez de données avec durée connue)"

# US 4.1, critère 1 : les 3 catégories exactes du cahier des charges
RESOLVED_OUTCOMES = {"resolved_auto", "resolved_manual"}
ESCALATED_OUTCOMES = {"escalated", "no_response"}


@dataclass
class ReportStats:
    total_detected: int = 0
    resolved_auto_count: int = 0
    resolved_manual_count: int = 0
    escalated_count: int = 0
    failed_count: int = 0
    success_rate_overall: float | None = None
    success_rate_by_action: dict = field(default_factory=dict)
    mttr_seconds: float | None = None
    mttr_sample_size: int = 0
    approval_counts: dict = field(default_factory=dict)
    intervention_history: list = field(default_factory=list)   # US 4.1, critère 2 : TOUT l'historique
    recent_failures: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_detected": self.total_detected,
            "resolved_auto_count": self.resolved_auto_count,
            "resolved_manual_count": self.resolved_manual_count,
            "escalated_count": self.escalated_count,
            "failed_count": self.failed_count,
            "success_rate_overall": self.success_rate_overall,
            "success_rate_by_action": self.success_rate_by_action,
            "mttr_seconds": self.mttr_seconds,
            "mttr_sample_size": self.mttr_sample_size,
            "approval_counts": self.approval_counts,
            "intervention_history": self.intervention_history,
            "recent_failures": self.recent_failures,
        }


class ReportingEngine:
    def __init__(self, incident_log_path: str = "incident_log.jsonl",
                 history_path: str = "remediation_history.jsonl",
                 audit_path: str = "approval_audit.jsonl"):
        self.incident_log_path = incident_log_path
        self.history_path = history_path
        self.audit_path = audit_path

    @staticmethod
    def _read_jsonl(path: str) -> list[dict]:
        if not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def compute_stats(self, history_limit: int = 100, recent_failures_limit: int = 10) -> ReportStats:
        incidents = self._read_jsonl(self.incident_log_path)
        history = self._read_jsonl(self.history_path)
        audit = self._read_jsonl(self.audit_path)

        stats = ReportStats(total_detected=len(incidents))

        # US 4.1, critère 1 : compteurs détecté / résolu / escaladé
        for inc in incidents:
            outcome = inc.get("outcome")
            if outcome == "resolved_auto":
                stats.resolved_auto_count += 1
            elif outcome == "resolved_manual":
                stats.resolved_manual_count += 1
            elif outcome in ESCALATED_OUTCOMES:
                stats.escalated_count += 1
            else:
                stats.failed_count += 1

        # US 4.1, critère 2 : historique complet, le plus récent en premier
        stats.intervention_history = sorted(incidents, key=lambda i: i["timestamp"], reverse=True)[:history_limit]

        # Taux de succès et MTTR (bonus, à partir de remediation_history.jsonl)
        if history:
            total_attempts = len(history)
            success_count = sum(1 for h in history if h["success"])
            stats.success_rate_overall = round(success_count / total_attempts, 3)

            by_action: dict[str, list[bool]] = {}
            for h in history:
                by_action.setdefault(h["action_id"], []).append(h["success"])
            stats.success_rate_by_action = {
                action: round(sum(results) / len(results), 3) for action, results in by_action.items()
            }

            durations = [h["duration_seconds"] for h in history if h.get("success") and h.get("duration_seconds") is not None]
            if durations:
                stats.mttr_seconds = round(statistics.mean(durations), 1)
                stats.mttr_sample_size = len(durations)

            failures = sorted((h for h in history if not h["success"]), key=lambda h: h["timestamp"], reverse=True)
            stats.recent_failures = failures[:recent_failures_limit]

        stats.approval_counts = self._count_approvals(audit)
        return stats

    @staticmethod
    def _count_approvals(audit: list[dict]) -> dict:
        counts: dict[str, int] = {}
        for entry in audit:
            status = entry.get("status", "unknown")
            counts[status] = counts.get(status, 0) + 1
        return counts

    # ------------------------------------------------------------------
    def generate_html(self, output_path: str = "dashboard.html", stats: ReportStats | None = None) -> str:
        stats = stats or self.compute_stats()
        html = _render_dashboard(stats)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
        return output_path


def _pct(value: float | None) -> str:
    return f"{value * 100:.0f}%" if value is not None else "N/A"


def _mttr_label(stats: ReportStats) -> str:
    if stats.mttr_seconds is None:
        return MTTR_UNAVAILABLE_LABEL
    minutes = stats.mttr_seconds / 60
    return f"{minutes:.1f} min (sur {stats.mttr_sample_size} résolution(s) mesurée(s))"


def _bar_row(label: str, rate: float) -> str:
    pct = round(rate * 100)
    color = "#2E7D5B" if pct >= 70 else ("#D98E04" if pct >= 40 else "#B3261E")
    return f"""
    <div class="bar-row">
      <span class="bar-label">{label}</span>
      <div class="bar-track"><div class="bar-fill" style="width:{pct}%;background:{color}"></div></div>
      <span class="bar-value">{pct}%</span>
    </div>"""


def _outcome_badge(outcome: str) -> str:
    labels = {
        "resolved_auto": ("Résolu (auto)", "#2E7D5B"),
        "resolved_manual": ("Résolu (manuel)", "#1C7293"),
        "escalated": ("Escaladé", "#D98E04"),
        "no_response": ("Sans réponse", "#D98E04"),
        "rejected": ("Rejeté", "#B3261E"),
        "failed": ("Échec", "#B3261E"),
    }
    label, color = labels.get(outcome, (outcome, "#6B7280"))
    return f'<span style="background:{color}20;color:{color};padding:2px 8px;border-radius:6px;font-size:0.8rem;font-weight:600;">{label}</span>'


def _history_row(inc: dict) -> str:
    import datetime
    ts = datetime.datetime.fromtimestamp(inc["timestamp"], tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""
    <tr>
      <td>{ts}</td>
      <td>{inc.get('service') or inc.get('entity', '')}</td>
      <td>{inc.get('error_category', '')}</td>
      <td>{inc.get('action_taken') or '—'}</td>
      <td>{_outcome_badge(inc.get('outcome', 'unknown'))}</td>
    </tr>"""


def _failure_row(f: dict) -> str:
    import datetime
    ts = datetime.datetime.fromtimestamp(f["timestamp"], tz=datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""
    <tr>
      <td>{ts}</td>
      <td>{f['entity']}</td>
      <td>{f['action_id']}</td>
      <td>{f['error_category']}</td>
    </tr>"""


def _render_dashboard(stats: ReportStats) -> str:
    bars = "".join(_bar_row(action, rate) for action, rate in sorted(
        stats.success_rate_by_action.items(), key=lambda kv: -kv[1]
    )) or '<p class="empty">Aucune donnée pour l’instant.</p>'

    history_rows = "".join(_history_row(i) for i in stats.intervention_history) or \
        '<tr><td colspan="5" class="empty">Aucun incident enregistré pour l’instant.</td></tr>'

    failures_rows = "".join(_failure_row(f) for f in stats.recent_failures) or \
        '<tr><td colspan="4" class="empty">Aucun échec récent 🎉</td></tr>'

    approval_items = "".join(
        f'<div class="stat-chip"><span class="chip-value">{count}</span><span class="chip-label">{status}</span></div>'
        for status, count in stats.approval_counts.items()
    ) or '<p class="empty">Aucune approbation enregistrée.</p>'

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>Dashboard — Agent DevOps Autonome</title>
<style>
  :root {{ --deep:#065A82; --teal:#1C7293; --midnight:#21295C; --bg:#F7F8FA; --text:#1A1A1A; --muted:#5A5A6A; }}
  body {{ font-family: -apple-system, Segoe UI, Calibri, sans-serif; background:var(--bg); color:var(--text); margin:0; padding:2rem; }}
  h1 {{ color:var(--midnight); font-size:1.8rem; margin-bottom:0.2rem; }}
  .subtitle {{ color:var(--muted); margin-bottom:2rem; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:1rem; margin-bottom:2rem; }}
  .card {{ background:white; border-radius:10px; padding:1.2rem; box-shadow:0 2px 8px rgba(0,0,0,0.08); }}
  .card .value {{ font-size:2rem; font-weight:700; color:var(--deep); }}
  .card .label {{ color:var(--muted); font-size:0.85rem; }}
  .section {{ background:white; border-radius:10px; padding:1.5rem; margin-bottom:1.5rem; box-shadow:0 2px 8px rgba(0,0,0,0.08); }}
  .section h2 {{ margin-top:0; font-size:1.1rem; color:var(--midnight); }}
  .bar-row {{ display:flex; align-items:center; gap:0.8rem; margin:0.6rem 0; }}
  .bar-label {{ width:160px; font-size:0.9rem; }}
  .bar-track {{ flex:1; background:#EEE; border-radius:6px; height:14px; overflow:hidden; }}
  .bar-fill {{ height:100%; border-radius:6px; }}
  .bar-value {{ width:40px; text-align:right; font-size:0.85rem; color:var(--muted); }}
  table {{ width:100%; border-collapse:collapse; font-size:0.9rem; }}
  th, td {{ text-align:left; padding:0.5rem; border-bottom:1px solid #EEE; }}
  th {{ color:var(--muted); font-weight:600; }}
  .stat-chip {{ display:inline-flex; flex-direction:column; align-items:center; background:#F0F1F5; border-radius:8px; padding:0.6rem 1rem; margin:0.3rem; }}
  .chip-value {{ font-weight:700; color:var(--midnight); }}
  .chip-label {{ font-size:0.75rem; color:var(--muted); }}
  .empty {{ color:var(--muted); font-style:italic; }}
  .scroll-table {{ max-height:400px; overflow-y:auto; }}
</style>
</head>
<body>
  <h1>Agent DevOps Autonome — Dashboard</h1>
  <p class="subtitle">Audit trail et reporting des interventions (US 4.1)</p>

  <div class="grid">
    <div class="card"><div class="value">{stats.total_detected}</div><div class="label">Incidents détectés</div></div>
    <div class="card"><div class="value">{stats.resolved_auto_count}</div><div class="label">Résolus automatiquement</div></div>
    <div class="card"><div class="value">{stats.resolved_manual_count}</div><div class="label">Résolus manuellement</div></div>
    <div class="card"><div class="value">{stats.escalated_count}</div><div class="label">Escaladés</div></div>
    <div class="card"><div class="value">{_mttr_label(stats).split(' ')[0]}</div><div class="label">MTTR</div></div>
  </div>

  <div class="section">
    <h2>Historique complet des interventions</h2>
    <div class="scroll-table">
      <table>
        <tr><th>Date</th><th>Service</th><th>Catégorie</th><th>Action entreprise</th><th>Résultat</th></tr>
        {history_rows}
      </table>
    </div>
  </div>

  <div class="section">
    <h2>Taux de succès par action</h2>
    {bars}
  </div>

  <div class="section">
    <h2>Approbations</h2>
    {approval_items}
  </div>

  <div class="section">
    <h2>Échecs récents (alertes)</h2>
    <table>
      <tr><th>Date</th><th>Entité</th><th>Action</th><th>Catégorie</th></tr>
      {failures_rows}
    </table>
  </div>
</body>
</html>"""
