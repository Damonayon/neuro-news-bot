"""bot.http — единая обёртка HTTP-вызовов: retry + circuit breaker + deadline.

Назначение:
1. **Retry с exponential backoff** (tenacity). На повторно-восстановимых ошибках
   (5xx, 408/429, ConnectionError, Timeout) ждём 1с → 2с → 4с → ... до N попыток.
2. **Circuit breaker (per-host)**. Если на один хост подряд произошло
   `FAILURE_THRESHOLD` ошибок — хост блокируется на `OPEN_DURATION_SEC`,
   все запросы к нему немедленно падают с `CircuitOpenError`. Это защищает
   и нас (не сжигаем минуты GH Actions), и удалённый сервис (не добиваем его).
3. **Deadline для всего пайплайна**. На старте `set_deadline(seconds)` задаёт
   глобальный таймбюджет; перед каждым вызовом проверяем, не превышен ли он.

Использование:
    from bot.http import HttpClient, set_deadline

    set_deadline(300)              # 5 минут на весь процесс
    client = HttpClient()
    resp = client.post(url, json={...}, timeout=15)
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from threading import Lock
from typing import Any
from urllib.parse import urlparse

import requests
from tenacity import (
    RetryError,
    Retrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from bot.logging_setup import get_logger

log = get_logger("bot.http")


# ─── Настройки по умолчанию (переопределяются в HttpClient) ──────────────────

DEFAULT_TIMEOUT_SEC = 30
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_BACKOFF_BASE_SEC = 1.0
DEFAULT_BACKOFF_MAX_SEC = 30.0

# Какие HTTP-статусы считать "временной" ошибкой (имеет смысл повторить)
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}

# Circuit breaker
FAILURE_THRESHOLD = 5  # после стольких подряд ошибок — открываем
OPEN_DURATION_SEC = 300  # сколько хост остаётся "блокированным"


# ─── Исключения ──────────────────────────────────────────────────────────────


class HttpError(Exception):
    """Базовое исключение HTTP-обёртки."""


class CircuitOpenError(HttpError):
    """Цепь разорвана: хост временно заблокирован circuit breaker'ом."""


class DeadlineExceededError(HttpError):
    """Превышен общий таймбюджет (deadline)."""


class RetryableHttpStatus(HttpError):
    """HTTP-ответ с retryable-статусом (5xx, 429 и т.п.). Триггерит retry."""

    def __init__(self, status: int, text: str, url: str):
        self.status = status
        self.text = text
        self.url = url
        super().__init__(f"HTTP {status} on {url}: {text[:200]}")


# ─── Deadline (глобальный) ───────────────────────────────────────────────────


class _Deadline:
    """Простой глобальный дедлайн. Кросс-платформенный (без signal)."""

    def __init__(self) -> None:
        self._expires_at: float | None = None
        self._lock = Lock()

    def set(self, seconds: float) -> None:
        with self._lock:
            self._expires_at = time.monotonic() + seconds

    def clear(self) -> None:
        with self._lock:
            self._expires_at = None

    def remaining(self) -> float | None:
        with self._lock:
            if self._expires_at is None:
                return None
            return max(0.0, self._expires_at - time.monotonic())

    def check(self) -> None:
        r = self.remaining()
        if r is not None and r <= 0:
            raise DeadlineExceededError("Превышен общий таймбюджет (deadline)")


_deadline = _Deadline()


def set_deadline(seconds: float) -> None:
    """Задать общий таймбюджет на весь процесс."""
    _deadline.set(seconds)
    log.info("Установлен deadline: %.0f сек", seconds)


def clear_deadline() -> None:
    _deadline.clear()


def deadline_remaining() -> float | None:
    return _deadline.remaining()


# ─── Circuit Breaker ─────────────────────────────────────────────────────────


@dataclass
class _HostState:
    consecutive_failures: int = 0
    opened_at: float | None = None  # время, когда цепь была разорвана


class _CircuitBreaker:
    """Per-host circuit breaker. Потокобезопасный."""

    def __init__(
        self,
        failure_threshold: int = FAILURE_THRESHOLD,
        open_duration_sec: float = OPEN_DURATION_SEC,
    ) -> None:
        self._states: dict[str, _HostState] = {}
        self._lock = Lock()
        self._threshold = failure_threshold
        self._open_duration = open_duration_sec

    def _state(self, host: str) -> _HostState:
        if host not in self._states:
            self._states[host] = _HostState()
        return self._states[host]

    def check(self, host: str) -> None:
        """Если хост заблокирован — бросает CircuitOpenError. Иначе ничего."""
        with self._lock:
            s = self._state(host)
            if s.opened_at is None:
                return
            elapsed = time.monotonic() - s.opened_at
            if elapsed < self._open_duration:
                remaining = int(self._open_duration - elapsed)
                raise CircuitOpenError(f"Circuit open для {host}: ещё {remaining} сек блокировки")
            # Время вышло — half-open: пробуем снова
            log.info("Circuit breaker: half-open пробую %s", host)
            s.opened_at = None
            s.consecutive_failures = 0

    def record_success(self, host: str) -> None:
        with self._lock:
            s = self._state(host)
            if s.consecutive_failures > 0 or s.opened_at is not None:
                log.info("Circuit breaker: сброшен для %s после успеха", host)
            s.consecutive_failures = 0
            s.opened_at = None

    def record_failure(self, host: str) -> None:
        with self._lock:
            s = self._state(host)
            s.consecutive_failures += 1
            if s.consecutive_failures >= self._threshold and s.opened_at is None:
                s.opened_at = time.monotonic()
                log.error(
                    "Circuit breaker: ОТКРЫТ для %s после %d ошибок подряд",
                    host,
                    s.consecutive_failures,
                )


_breaker = _CircuitBreaker()


# ─── Какие ошибки ретраить ───────────────────────────────────────────────────


_RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
    RetryableHttpStatus,
)


def _is_retryable(exc: BaseException) -> bool:
    return isinstance(exc, _RETRYABLE_EXCEPTIONS)


# ─── HttpClient ──────────────────────────────────────────────────────────────


class HttpClient:
    """HTTP-клиент с retry + circuit breaker + deadline."""

    def __init__(
        self,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_base: float = DEFAULT_BACKOFF_BASE_SEC,
        backoff_max: float = DEFAULT_BACKOFF_MAX_SEC,
        default_timeout: float = DEFAULT_TIMEOUT_SEC,
        user_agent: str = "neuro-news-bot/1.0 (+https://github.com)",
    ) -> None:
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._default_timeout = default_timeout
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": user_agent})

    # ─── публичные методы ─────────────────────────────────────────────

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        return self.request("POST", url, **kwargs)

    def request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        """Выполняет запрос с retry и circuit breaker.
        Все retryable ошибки и 5xx ретраятся, прочее (4xx) — бросает HTTPError из requests."""

        host = urlparse(url).netloc or url
        kwargs.setdefault("timeout", self._default_timeout)

        # Подгоняем timeout под оставшийся deadline (если задан)
        remaining = _deadline.remaining()
        if remaining is not None:
            kwargs["timeout"] = min(kwargs["timeout"], max(1.0, remaining))

        def _do_request() -> requests.Response:
            _deadline.check()
            _breaker.check(host)
            resp = self._session.request(method, url, **kwargs)
            if resp.status_code in RETRYABLE_STATUS:
                raise RetryableHttpStatus(resp.status_code, resp.text, url)
            return resp

        try:
            for attempt in Retrying(
                stop=stop_after_attempt(self._max_attempts),
                wait=wait_exponential(multiplier=self._backoff_base, max=self._backoff_max),
                retry=retry_if_exception(_is_retryable),
                reraise=True,
            ):
                with attempt:
                    try:
                        resp = _do_request()
                    except _RETRYABLE_EXCEPTIONS as exc:
                        _breaker.record_failure(host)
                        log.warning(
                            "HTTP retryable error (%s) %s %s: %s",
                            type(exc).__name__,
                            method,
                            host,
                            str(exc)[:150],
                        )
                        raise
                    except (CircuitOpenError, DeadlineExceededError):
                        # Эти НЕ ретраим — сразу наверх
                        raise
                    except requests.RequestException as exc:
                        # 4xx и прочее — без retry, без circuit failure
                        log.warning("HTTP non-retryable %s %s: %s", method, host, exc)
                        raise
                    else:
                        _breaker.record_success(host)
                        return resp
        except RetryError as exc:
            # Не должно случиться при reraise=True, но на всякий
            last_exc = exc.last_attempt.exception()
            if last_exc is not None:
                raise last_exc from exc
            raise

        # Сюда мы не доходим — Retrying всегда либо вернёт, либо бросит
        raise RuntimeError("unreachable")  # pragma: no cover


# ─── Удобный синглтон ────────────────────────────────────────────────────────


_default_client: HttpClient | None = None


def default_client() -> HttpClient:
    global _default_client
    if _default_client is None:
        _default_client = HttpClient()
    return _default_client


def http_get(url: str, **kwargs: Any) -> requests.Response:
    return default_client().get(url, **kwargs)


def http_post(url: str, **kwargs: Any) -> requests.Response:
    return default_client().post(url, **kwargs)


__all__: Sequence[str] = (
    "CircuitOpenError",
    "DeadlineExceededError",
    "HttpClient",
    "HttpError",
    "RetryableHttpStatus",
    "clear_deadline",
    "deadline_remaining",
    "default_client",
    "http_get",
    "http_post",
    "set_deadline",
)
