"""
Tests unitaires pour loki_client.py — US 2.1 : Agrégation des logs contextuels.

Ces tests utilisent un mock de `requests.get` : aucun appel réseau réel
n'est effectué, donc pas besoin d'un vrai Loki pour les lancer.

Lancer avec :
    python3 -m pytest test_loki_client.py -v
"""

from unittest.mock import patch, MagicMock
import time

import config
import loki_client


def _fake_loki_response(lines: list[str]):
    """Construit une réponse Loki factice à partir d'une liste de messages."""
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = {
        "data": {
            "result": [
                {
                    "values": [
                        [str(int(time.time() * 1e9) - i * 1000), line]
                        for i, line in enumerate(reversed(lines))
                    ]
                }
            ]
        }
    }
    return resp


class TestFetchLogsQueryConstruction:
    """Vérifie que la requête envoyée à Loki respecte les critères US 2.1."""

    @patch("loki_client.requests.get")
    def test_query_includes_level_filter(self, mock_get):
        mock_get.return_value = _fake_loki_response(["ERROR: boom"])

        loki_client.fetch_logs(namespace="monitoring", pod="api-payment")

        called_query = mock_get.call_args.kwargs["params"]["query"]
        for level in config.LOG_LEVELS:
            assert level in called_query, (
                f"Le niveau '{level}' devrait apparaître dans le filtre LogQL : {called_query}"
            )

    @patch("loki_client.requests.get")
    def test_query_includes_pod_selector(self, mock_get):
        mock_get.return_value = _fake_loki_response([])

        loki_client.fetch_logs(namespace="monitoring", pod="api-payment")

        called_query = mock_get.call_args.kwargs["params"]["query"]
        assert 'pod=~"api-payment' in called_query

    @patch("loki_client.requests.get")
    def test_limit_is_500(self, mock_get):
        mock_get.return_value = _fake_loki_response([])

        loki_client.fetch_logs(namespace="monitoring")

        called_limit = mock_get.call_args.kwargs["params"]["limit"]
        assert called_limit == 500 == config.MAX_LOG_LINES

    @patch("loki_client.requests.get")
    def test_timeout_is_5_seconds(self, mock_get):
        mock_get.return_value = _fake_loki_response([])

        loki_client.fetch_logs(namespace="monitoring")

        called_timeout = mock_get.call_args.kwargs["timeout"]
        assert called_timeout == 5 == config.LOKI_REQUEST_TIMEOUT_SECONDS


class TestFetchLogsSlowResponseWarning:
    """Vérifie qu'un dépassement du budget de 5s est bien signalé."""

    @patch("loki_client.requests.get")
    def test_logs_warning_when_response_slow(self, mock_get, caplog):
        def slow_get(*args, **kwargs):
            time.sleep(0.05)  # simule une réponse lente
            return _fake_loki_response(["ERROR: boom"])

        mock_get.side_effect = slow_get

        # On abaisse temporairement le seuil pour déclencher le warning
        # sans avoir à dormir 5 vraies secondes dans le test.
        original_timeout = loki_client.LOKI_REQUEST_TIMEOUT_SECONDS
        loki_client.LOKI_REQUEST_TIMEOUT_SECONDS = 0.01
        try:
            with caplog.at_level("WARNING"):
                loki_client.fetch_logs(namespace="monitoring")
        finally:
            loki_client.LOKI_REQUEST_TIMEOUT_SECONDS = original_timeout

        assert any("plus lente que le budget" in record.message for record in caplog.records)


class TestFetchLogsOutput:
    """Vérifie le comportement fonctionnel de base (ordre, format)."""

    @patch("loki_client.requests.get")
    def test_returns_lines_in_chronological_order(self, mock_get):
        mock_get.return_value = _fake_loki_response(["oldest", "middle", "newest"])

        result = loki_client.fetch_logs(namespace="monitoring")

        assert result == ["oldest", "middle", "newest"]

    @patch("loki_client.requests.get")
    def test_empty_result_returns_empty_list(self, mock_get):
        mock_get.return_value = _fake_loki_response([])

        result = loki_client.fetch_logs(namespace="monitoring")

        assert result == []

    @patch("loki_client.requests.get")
    def test_raises_on_http_error(self, mock_get):
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception("HTTP 500")
        mock_get.return_value = resp

        try:
            loki_client.fetch_logs(namespace="monitoring")
            assert False, "Une exception aurait dû être levée"
        except Exception:
            pass
