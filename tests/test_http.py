"""Тесты bot.http — retry, circuit breaker, deadline.

Используем unittest.mock для подмены requests.Session.request — реальные
сетевые вызовы в тестах не делаем.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

import bot.http
from bot.http import (
    CircuitOpenError,
    DeadlineExceededError,
    HttpClient,
    RetryableHttpStatus,
    clear_deadline,
    set_deadline,
)


@pytest.fixture(autouse=True)
def _reset_breaker() -> None:
    """Перед каждым тестом — чистый circuit breaker и deadline."""
    bot.http._breaker._states.clear()
    clear_deadline()


def _mock_response(status: int = 200, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    return resp


class TestRetry:
    def test_success_first_try(self) -> None:
        client = HttpClient(max_attempts=3, backoff_base=0.01)
        with patch.object(client._session, "request", return_value=_mock_response(200)) as mock:
            resp = client.get("https://example.com/x")
            assert resp.status_code == 200
            assert mock.call_count == 1

    def test_retries_on_503(self) -> None:
        client = HttpClient(max_attempts=3, backoff_base=0.01)
        # Первые 2 — 503, третий — 200
        responses = [_mock_response(503), _mock_response(503), _mock_response(200)]
        with patch.object(client._session, "request", side_effect=responses):
            resp = client.get("https://example.com/x")
            assert resp.status_code == 200

    def test_exhausts_attempts(self) -> None:
        client = HttpClient(max_attempts=3, backoff_base=0.01)
        with patch.object(client._session, "request", return_value=_mock_response(500)):
            with pytest.raises(RetryableHttpStatus) as exc_info:
                client.get("https://example.com/x")
            assert exc_info.value.status == 500

    def test_404_not_retried(self) -> None:
        """4xx (кроме 408/429) — без retry, возвращается как обычный response."""
        client = HttpClient(max_attempts=5, backoff_base=0.01)
        with patch.object(client._session, "request", return_value=_mock_response(404)) as mock:
            resp = client.get("https://example.com/x")
            assert resp.status_code == 404
            assert mock.call_count == 1  # без retry

    def test_429_retried(self) -> None:
        client = HttpClient(max_attempts=3, backoff_base=0.01)
        responses = [_mock_response(429), _mock_response(200)]
        with patch.object(client._session, "request", side_effect=responses):
            resp = client.get("https://example.com/x")
            assert resp.status_code == 200

    def test_connection_error_retried(self) -> None:
        client = HttpClient(max_attempts=3, backoff_base=0.01)
        responses: list[Any] = [requests.ConnectionError("boom"), _mock_response(200)]
        with patch.object(client._session, "request", side_effect=responses):
            resp = client.get("https://example.com/x")
            assert resp.status_code == 200


class TestCircuitBreaker:
    def test_opens_after_5_failures(self) -> None:
        client = HttpClient(max_attempts=1, backoff_base=0.01)
        with patch.object(client._session, "request", return_value=_mock_response(500)):
            # Первые 5 — нормальные RetryableHttpStatus (5 ошибок копятся в брокере)
            for _ in range(5):
                with pytest.raises(RetryableHttpStatus):
                    client.get("https://example.com/x")
            # 6-й вызов — circuit уже OPEN, моментальный отказ
            with pytest.raises(CircuitOpenError):
                client.get("https://example.com/x")

    def test_success_resets_breaker(self) -> None:
        client = HttpClient(max_attempts=1, backoff_base=0.01)
        # 3 ошибки, потом успех — breaker не должен открыться
        responses = [
            _mock_response(500),
            _mock_response(500),
            _mock_response(500),
            _mock_response(200),
            _mock_response(500),
            _mock_response(500),
        ]
        with patch.object(client._session, "request", side_effect=responses):
            for _ in range(3):
                with pytest.raises(RetryableHttpStatus):
                    client.get("https://example.com/x")
            assert client.get("https://example.com/x").status_code == 200
            # После успеха счётчик сбросился — ещё 2 ошибки не откроют брокер
            for _ in range(2):
                with pytest.raises(RetryableHttpStatus):
                    client.get("https://example.com/x")
            # Не CircuitOpenError, потому что breaker сбросился
            assert bot.http._breaker._states["example.com"].opened_at is None


class TestDeadline:
    def test_set_and_check(self) -> None:
        set_deadline(0.05)
        time.sleep(0.1)
        client = HttpClient(max_attempts=3, backoff_base=0.01)
        with (
            patch.object(client._session, "request", return_value=_mock_response(200)),
            pytest.raises(DeadlineExceededError),
        ):
            client.get("https://example.com/x")

    def test_no_deadline_set(self) -> None:
        """Без set_deadline всё работает как обычно."""
        client = HttpClient(max_attempts=3, backoff_base=0.01)
        with patch.object(client._session, "request", return_value=_mock_response(200)):
            assert client.get("https://example.com/x").status_code == 200

    def test_clear_deadline(self) -> None:
        set_deadline(0.05)
        time.sleep(0.1)
        clear_deadline()
        client = HttpClient(max_attempts=3, backoff_base=0.01)
        with patch.object(client._session, "request", return_value=_mock_response(200)):
            assert client.get("https://example.com/x").status_code == 200
