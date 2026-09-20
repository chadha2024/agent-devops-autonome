import json
import time
import pytest

from reporting_engine import ReportingEngine


def write_jsonl(path, entries):
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


@pytest.fixture
def engine(tmp_path):
    return ReportingEngine(
        history_path=str(tmp_path / "history.jsonl"),
        audit_path=str(tmp_path / "audit.jsonl"),
    )


class TestFichiersVides:
    def test_aucun_fichier_ne_plante(self, engine):
        stats = engine.compute_stats()
        assert stats.total_attempts == 0
        assert stats.success_rate_overall is None
        assert stats.mttr_seconds is None


class TestTauxDeSucces:
    def test_calcul_global_correct(self, engine):
        write_jsonl(engine.history_path, [
            {"timestamp": 1, "entity": "a", "error_category": "OutOfMemory", "action_id": "restart_pod", "success": True, "duration_seconds": None},
            {"timestamp": 2, "entity": "b", "error_category": "OutOfMemory", "action_id": "restart_pod", "success": True, "duration_seconds": None},
            {"timestamp": 3, "entity": "c", "error_category": "OutOfMemory", "action_id": "restart_pod", "success": False, "duration_seconds": None},
        ])
        stats = engine.compute_stats()
        assert stats.total_attempts == 3
        assert stats.success_count == 2
        assert stats.failure_count == 1
        assert stats.success_rate_overall == pytest.approx(2 / 3, abs=0.001)

    def test_taux_par_action_separes(self, engine):
        write_jsonl(engine.history_path, [
            {"timestamp": 1, "entity": "a", "error_category": "OutOfMemory", "action_id": "restart_pod", "success": True, "duration_seconds": None},
            {"timestamp": 2, "entity": "a", "error_category": "OutOfMemory", "action_id": "rollback", "success": False, "duration_seconds": None},
        ])
        stats = engine.compute_stats()
        assert stats.success_rate_by_action["restart_pod"] == 1.0
        assert stats.success_rate_by_action["rollback"] == 0.0


class TestMTTR:
    def test_mttr_ignore_les_entrees_sans_duree(self, engine):
        write_jsonl(engine.history_path, [
            {"timestamp": 100, "entity": "a", "error_category": "x", "action_id": "restart_pod", "success": True, "duration_seconds": None},
        ])
        stats = engine.compute_stats()
        assert stats.mttr_seconds is None
        assert stats.mttr_sample_size == 0

    def test_mttr_moyenne_des_durees_reussies(self, engine):
        write_jsonl(engine.history_path, [
            {"timestamp": 100, "entity": "a", "error_category": "x", "action_id": "restart_pod", "success": True, "duration_seconds": 60},
            {"timestamp": 200, "entity": "b", "error_category": "x", "action_id": "restart_pod", "success": True, "duration_seconds": 120},
        ])
        stats = engine.compute_stats()
        assert stats.mttr_seconds == 90.0
        assert stats.mttr_sample_size == 2

    def test_mttr_ignore_les_echecs_meme_avec_duree(self, engine):
        """Un échec n'a pas 'réparé' quoi que ce soit -> ne doit pas
        compter dans le temps moyen de réparation."""
        write_jsonl(engine.history_path, [
            {"timestamp": 100, "entity": "a", "error_category": "x", "action_id": "restart_pod", "success": True, "duration_seconds": 60},
            {"timestamp": 200, "entity": "b", "error_category": "x", "action_id": "restart_pod", "success": False, "duration_seconds": 99999},
        ])
        stats = engine.compute_stats()
        assert stats.mttr_seconds == 60.0
        assert stats.mttr_sample_size == 1


class TestApprobations:
    def test_compte_par_statut(self, engine):
        write_jsonl(engine.audit_path, [
            {"status": "auto_approved"}, {"status": "auto_approved"}, {"status": "approved"}, {"status": "rejected"},
        ])
        stats = engine.compute_stats()
        assert stats.approval_counts == {"auto_approved": 2, "approved": 1, "rejected": 1}


class TestEchecsRecents:
    def test_tries_du_plus_recent_au_plus_ancien(self, engine):
        write_jsonl(engine.history_path, [
            {"timestamp": 100, "entity": "old", "error_category": "x", "action_id": "restart_pod", "success": False, "duration_seconds": None},
            {"timestamp": 300, "entity": "recent", "error_category": "x", "action_id": "restart_pod", "success": False, "duration_seconds": None},
            {"timestamp": 200, "entity": "middle", "error_category": "x", "action_id": "restart_pod", "success": False, "duration_seconds": None},
        ])
        stats = engine.compute_stats()
        assert [f["entity"] for f in stats.recent_failures] == ["recent", "middle", "old"]

    def test_limite_respectee(self, engine):
        write_jsonl(engine.history_path, [
            {"timestamp": i, "entity": f"e{i}", "error_category": "x", "action_id": "restart_pod", "success": False, "duration_seconds": None}
            for i in range(20)
        ])
        stats = engine.compute_stats(recent_failures_limit=5)
        assert len(stats.recent_failures) == 5

    def test_succes_n_apparaissent_pas_dans_les_echecs(self, engine):
        write_jsonl(engine.history_path, [
            {"timestamp": 1, "entity": "a", "error_category": "x", "action_id": "restart_pod", "success": True, "duration_seconds": 10},
        ])
        stats = engine.compute_stats()
        assert stats.recent_failures == []


class TestDashboardHtml:
    def test_genere_un_fichier_html_valide(self, engine, tmp_path):
        write_jsonl(engine.history_path, [
            {"timestamp": time.time(), "entity": "crash-test", "error_category": "NetworkTimeout",
             "action_id": "restart_pod", "success": True, "duration_seconds": 45},
        ])
        output = str(tmp_path / "dashboard.html")
        path = engine.generate_html(output_path=output)

        assert path == output
        content = open(output, encoding="utf-8").read()
        assert "<!DOCTYPE html>" in content
        assert "crash-test" not in content  # pas affiché dans les échecs, car c'est un succès
        assert "restart_pod" in content  # apparaît dans la barre de taux de succès

    def test_dashboard_avec_donnees_vides_ne_plante_pas(self, engine, tmp_path):
        output = str(tmp_path / "dashboard.html")
        path = engine.generate_html(output_path=output)
        assert open(path, encoding="utf-8").read().startswith("<!DOCTYPE html>")

    def test_echec_apparait_bien_dans_le_html(self, engine, tmp_path):
        write_jsonl(engine.history_path, [
            {"timestamp": time.time(), "entity": "crash-test", "error_category": "NetworkTimeout",
             "action_id": "restart_pod", "success": False, "duration_seconds": None},
        ])
        output = str(tmp_path / "dashboard.html")
        engine.generate_html(output_path=output)
        content = open(output, encoding="utf-8").read()
        assert "crash-test" in content
