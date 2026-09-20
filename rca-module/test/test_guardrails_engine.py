import yaml
import pytest

from guardrails_engine import GuardrailsEngine


@pytest.fixture
def policy_file(tmp_path):
    """Copie de la vraie politique dans un fichier temporaire, pour
    pouvoir la modifier librement dans les tests (ex: activer le kill
    switch) sans toucher au vrai fichier du projet."""
    with open("guardrails_policy.yaml", "r", encoding="utf-8") as f:
        policy = yaml.safe_load(f)
    path = tmp_path / "guardrails_policy.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(policy, f)
    return path


@pytest.fixture
def engine(policy_file):
    return GuardrailsEngine(policy_path=str(policy_file))


class TestListeBlanche:
    def test_action_autorisee_passe(self, engine):
        result = engine.check("restart_pod")
        assert result.allowed is True
        assert result.reasons == []

    def test_action_hors_liste_blanche_bloquee(self, engine):
        result = engine.check("rollback")  # volontairement pas dans la liste blanche
        assert result.allowed is False
        assert any("liste blanche" in r for r in result.reasons)

    def test_action_inconnue_bloquee_par_defaut(self, engine):
        """Une action qui n'existe même pas dans le catalogue ne doit
        jamais être autorisée par défaut -> sécurité par défaut."""
        result = engine.check("action_jamais_vue")
        assert result.allowed is False


class TestLimitesDeScope:
    def test_sous_la_limite_de_pods_passe(self, engine):
        result = engine.check("restart_pod", pods_affected=3)
        assert result.allowed is True

    def test_depassement_pods_bloque(self, engine):
        result = engine.check("restart_pod", pods_affected=10)  # > 5, la limite
        assert result.allowed is False
        assert any("pods affectés" in r for r in result.reasons)

    def test_scale_factor_dans_la_limite_passe(self, engine):
        result = engine.check("scale_horizontally", scale_factor=2.0)
        assert result.allowed is True

    def test_scale_factor_excessif_bloque(self, engine):
        result = engine.check("scale_horizontally", scale_factor=10.0)  # > 3.0
        assert result.allowed is False
        assert any("échelle" in r for r in result.reasons)


class TestKillSwitch:
    def test_kill_switch_desactive_par_defaut(self, engine):
        assert engine.is_kill_switch_active() is False

    def test_kill_switch_bloque_meme_une_action_valide(self, engine, policy_file):
        # Action normalement 100% valide (liste blanche + scope OK)
        assert engine.check("restart_pod").allowed is True

        # On active le kill switch entre-temps (simule un humain qui l'active)
        with open(policy_file, "r", encoding="utf-8") as f:
            policy = yaml.safe_load(f)
        policy["kill_switch"]["enabled"] = True
        with open(policy_file, "w", encoding="utf-8") as f:
            yaml.safe_dump(policy, f)

        result = engine.check("restart_pod")
        assert result.allowed is False
        assert any("kill switch" in r for r in result.reasons)

    def test_kill_switch_pris_en_compte_sans_redemarrer(self, engine, policy_file):
        """Le rechargement doit être immédiat (pas de cache figé au
        démarrage) -> un humain doit pouvoir stopper l'agent EN COURS
        de route, pas seulement avant qu'il démarre."""
        with open(policy_file, "r", encoding="utf-8") as f:
            policy = yaml.safe_load(f)
        policy["kill_switch"]["enabled"] = True
        with open(policy_file, "w", encoding="utf-8") as f:
            yaml.safe_dump(policy, f)

        assert engine.is_kill_switch_active() is True  # même instance, pas de recréation


class TestMotifsMultiples:
    def test_plusieurs_violations_toutes_signalees_ensemble(self, engine):
        """Une action hors liste blanche ET hors limite de scope doit
        remonter les DEUX raisons d'un coup, pas juste la première."""
        result = engine.check("action_inconnue", pods_affected=100)
        assert result.allowed is False
        assert len(result.reasons) >= 2

    def test_to_dict_est_json_serialisable(self, engine):
        import json
        result = engine.check("restart_pod")
        json.dumps(result.to_dict())
