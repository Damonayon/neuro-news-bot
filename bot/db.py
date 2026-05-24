"""bot.db — движок SQLAlchemy и фабрика сессий.

Использование:
    from bot.db import session_scope, init_db

    init_db()                   # создаёт таблицы при первом запуске
    with session_scope() as s:  # контекстный менеджер с авто-commit/rollback
        s.add(obj)
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from bot.config import DATA_DIR, get_settings
from bot.models import Base


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """Возвращает синглтон Engine. При первом вызове создаёт его."""
    global _engine
    if _engine is None:
        settings = get_settings()
        # Для SQLite создаём каталог под БД, если его нет
        if settings.db_url.startswith("sqlite:"):
            DATA_DIR.mkdir(parents=True, exist_ok=True)

        _engine = create_engine(
            settings.db_url,
            echo=False,
            future=True,
            # Для SQLite важно: разрешаем использовать из разных потоков
            connect_args={"check_same_thread": False}
            if settings.db_url.startswith("sqlite:")
            else {},
        )

        # Для SQLite: включаем WAL (одновременные читатели + один писатель)
        # и foreign keys (по умолчанию выключены).
        if settings.db_url.startswith("sqlite:"):
            _enable_sqlite_pragmas(_engine)

    return _engine


def _enable_sqlite_pragmas(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()


def get_session_factory() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(),
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            future=True,
        )
    return _SessionLocal


@contextmanager
def session_scope() -> Iterator[Session]:
    """Контекстный менеджер: открывает сессию, коммитит при выходе,
    откатывает при исключении."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Создаёт все таблицы (если их нет). Идемпотентно — можно звать при каждом запуске."""
    Base.metadata.create_all(bind=get_engine())


def db_path() -> Path | None:
    """Возвращает путь к SQLite-файлу или None, если БД не файловая."""
    settings = get_settings()
    if settings.db_url.startswith("sqlite:///"):
        return Path(settings.db_url.replace("sqlite:///", "", 1))
    return None
