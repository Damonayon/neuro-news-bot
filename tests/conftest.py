"""Pytest fixtures: общая инфраструктура для всех тестов.

Задаёт env-переменные ДО загрузки bot.config (через autouse-фикстуру с уровня сессии).
Каждый тест получает чистую in-memory SQLite-БД через fixture `db_session`.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

# Корень проекта в sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ─── Env-переменные ДО любого импорта bot.* ──────────────────────────────────
# Это критично: pydantic-settings читает env при первом обращении к get_settings().

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test:fake-token")
os.environ.setdefault("TELEGRAM_MODERATOR_ID", "544843065")
os.environ.setdefault("TELEGRAM_CHANNEL_ID", "@test_channel")
os.environ.setdefault("GH_MODELS_TOKEN", "test-pat")
os.environ.setdefault("CHANNEL_TOPIC", "Test Channel")
os.environ.setdefault("CHANNEL_NICHE", "test niche")
os.environ.setdefault("CHANNEL_LANG", "русский")
os.environ.setdefault("SENTRY_DSN", "")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("DB_URL", "sqlite:///:memory:")


# ─── Фикстура: in-memory БД ──────────────────────────────────────────────────


@pytest.fixture
def db_session() -> Iterator:  # type: ignore[type-arg]
    """Чистая in-memory БД для каждого теста.

    Engine создаётся заново, чтобы тесты были полностью изолированы.
    """
    # Сброс синглтонов между тестами
    import bot.db

    bot.db._engine = None
    bot.db._SessionLocal = None
    # Сброс кеша pydantic-settings
    import bot.config

    bot.config._settings = None

    # Принудительно используем in-memory для этого теста
    os.environ["DB_URL"] = "sqlite:///:memory:"

    from bot.db import init_db, session_scope

    init_db()
    with session_scope() as session:
        yield session
