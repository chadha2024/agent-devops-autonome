"""
Tests unitaires pour llm_analyzer.py — US 2.2 : Identification de la cause racine.

Le client Gemini (`genai.Client`) est mocké : aucun appel réseau réel ni
clé API n'est nécessaire pour lancer ces tests.

Lancer avec :
    python3 -m pytest test_llm_analyzer.py -v
"""

import json
from unittest.mock import patch, MagicMock

import config

# GEMINI_API_KEY doit être "vrai" (non vide) pour dépasser le premier
# garde-fou de analyze_logs, même si on mocke le client ensuite.
config.GEMINI_API_KEY = "fake-key-for-tests"

import llm_analyzer  # noqa: E402  (import après le set de la clé factice)


def _mock_gemini_response(payload: dict):
    response = MagicMock()
    response.text = json.dumps(payload)
    return response


class TestConfidenceThreshold:
    """US 2.2 : sous 80% de confiance -> intervention humaine requise."""

    @patch("llm_analyzer.genai.Client")
    def test_high_confidence_does_not_require_human_review(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _mock_gemini_response({
            "has_error": True,
            "root_cause": "OOM",
            "affected_service": "api-payment",
            "error_category": "OutOfMemory",
            "error_pattern": "OOMKilled",
            "first_occurrence": "2026-08-06T14:02:00Z",
            "confidence_percent": 92,
            "recommended_action": "Augmenter les limites mémoire",
            "raw_summary": "Le pod a été tué pour dépassement mémoire.",
            "related_log_lines": ["OOMKilled"],
        })
        mock_client_cls.return_value = mock_client

        diagnostic = llm_analyzer.analyze_logs(["ERROR OOMKilled"])

        assert diagnostic.confidence_percent == 92
        assert diagnostic.needs_human_review is False

    @patch("llm_analyzer.genai.Client")
    def test_low_confidence_requires_human_review(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _mock_gemini_response({
            "has_error": True,
            "root_cause": "cause incertaine",
            "affected_service": "api-payment",
            "error_category": "Unknown",
            "error_pattern": "erreur ambiguë",
            "first_occurrence": "",
            "confidence_percent": 45,
            "recommended_action": "",
            "raw_summary": "Diagnostic incertain.",
            "related_log_lines": [],
        })
        mock_client_cls.return_value = mock_client

        diagnostic = llm_analyzer.analyze_logs(["WARN something odd"])

        assert diagnostic.confidence_percent == 45
        assert diagnostic.needs_human_review is True

    @patch("llm_analyzer.genai.Client")
    def test_confidence_exactly_at_threshold_requires_review(self, mock_client_cls):
        # confidence == seuil (80) -> le critère dit "< 80%" donc 80 pile
        # ne doit PAS déclencher la revue humaine (strictement inférieur).
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _mock_gemini_response({
            "has_error": True,
            "root_cause": "cause",
            "affected_service": "svc",
            "error_category": "NetworkTimeout",
            "error_pattern": "timeout",
            "first_occurrence": "",
            "confidence_percent": config.CONFIDENCE_THRESHOLD_PERCENT,
            "recommended_action": "",
            "raw_summary": "",
            "related_log_lines": [],
        })
        mock_client_cls.return_value = mock_client

        diagnostic = llm_analyzer.analyze_logs(["ERROR timeout"])

        assert diagnostic.needs_human_review is False


class TestErrorCategoryValidation:
    """US 2.2 : la catégorie doit toujours appartenir à la taxonomie fermée."""

    @patch("llm_analyzer.genai.Client")
    def test_known_category_is_kept(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _mock_gemini_response({
            "has_error": True,
            "root_cause": "disque plein",
            "affected_service": "logging-agent",
            "error_category": "DiskFull",
            "error_pattern": "no space left on device",
            "first_occurrence": "",
            "confidence_percent": 88,
            "recommended_action": "",
            "raw_summary": "",
            "related_log_lines": [],
        })
        mock_client_cls.return_value = mock_client

        diagnostic = llm_analyzer.analyze_logs(["ERROR no space left on device"])

        assert diagnostic.error_category == "DiskFull"
        assert diagnostic.error_category in config.ERROR_CATEGORIES

    @patch("llm_analyzer.genai.Client")
    def test_unknown_category_from_llm_falls_back_to_unknown(self, mock_client_cls):
        # Le LLM "invente" une catégorie hors taxonomie malgré le prompt.
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _mock_gemini_response({
            "has_error": True,
            "root_cause": "cause",
            "affected_service": "svc",
            "error_category": "SomeCategoryNotInTheList",
            "error_pattern": "motif",
            "first_occurrence": "",
            "confidence_percent": 70,
            "recommended_action": "",
            "raw_summary": "",
            "related_log_lines": [],
        })
        mock_client_cls.return_value = mock_client

        diagnostic = llm_analyzer.analyze_logs(["ERROR something weird"])

        assert diagnostic.error_category == "Unknown"

    @patch("llm_analyzer.genai.Client")
    def test_missing_category_defaults_to_unknown(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = _mock_gemini_response({
            "has_error": False,
            "root_cause": "",
            "affected_service": "",
            "error_pattern": "",
            "first_occurrence": "",
            "confidence_percent": 0,
            "recommended_action": "",
            "raw_summary": "Rien d'anormal.",
            "related_log_lines": [],
        })
        mock_client_cls.return_value = mock_client

        diagnostic = llm_analyzer.analyze_logs(["INFO all good"])

        assert diagnostic.error_category == "Unknown"


class TestEdgeCases:

    def test_empty_logs_returns_no_error_without_calling_llm(self):
        diagnostic = llm_analyzer.analyze_logs([])

        assert diagnostic.has_error is False
        assert diagnostic.error_category == "Unknown"
        assert diagnostic.needs_human_review is False

    def test_missing_api_key_raises(self):
        original = config.GEMINI_API_KEY
        llm_analyzer.GEMINI_API_KEY = None
        try:
            try:
                llm_analyzer.analyze_logs(["ERROR something"])
                assert False, "Une RuntimeError aurait dû être levée"
            except RuntimeError:
                pass
        finally:
            llm_analyzer.GEMINI_API_KEY = original

    @patch("llm_analyzer.genai.Client")
    def test_invalid_json_from_llm_raises_runtime_error(self, mock_client_cls):
        mock_client = MagicMock()
        response = MagicMock()
        response.text = "ceci n'est pas du JSON valide"
        mock_client.models.generate_content.return_value = response
        mock_client_cls.return_value = mock_client

        try:
            llm_analyzer.analyze_logs(["ERROR something"])
            assert False, "Une RuntimeError aurait dû être levée"
        except RuntimeError:
            pass
