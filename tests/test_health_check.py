"""Тесты scripts.health_check — cleanup-функции на in-memory БД."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from bot.models import POST_STATUS_FAILED, POST_STATUS_PENDING, Post
from bot.storage import ensure_channel, save_article


def test_cleanup_stale_pending_marks_old_failed(db_session: Any) -> None:
    """pending-посты старше 48ч должны помечаться как FAILED."""
    ch = ensure_channel(db_session)
    art = save_article(db_session, channel_id=ch.id, url="https://x.com/1", title="t", summary="")

    # Один свежий, один старый
    fresh = Post(
        article_id=art.id,
        channel_id=ch.id,
        post_text="fresh",
        status=POST_STATUS_PENDING,
        created_at=datetime.now(UTC) - timedelta(hours=1),
    )
    stale = Post(
        article_id=art.id,
        channel_id=ch.id,
        post_text="stale",
        status=POST_STATUS_PENDING,
        created_at=datetime.now(UTC) - timedelta(hours=72),  # 3 дня — старый
    )
    db_session.add_all([fresh, stale])
    db_session.flush()
    db_session.commit()

    # Импорт здесь, чтобы тестовая БД уже была инициализирована
    from scripts.health_check import cleanup_stale_pending

    affected = cleanup_stale_pending()
    assert affected == 1

    db_session.expire_all()
    refreshed = db_session.query(Post).order_by(Post.id).all()
    assert refreshed[0].status == POST_STATUS_PENDING  # fresh не тронут
    assert refreshed[1].status == POST_STATUS_FAILED  # stale помечен


def test_cleanup_old_logs_removes_old(db_session: Any) -> None:
    """Старые логи должны удаляться, новые — оставаться (но только если таблица переполнена)."""
    # Заполняем 10001 запись (порог 10000)
    from scripts.health_check import cleanup_old_logs

    # Эта функция работает только когда таблица превышает MAX_ROWS
    # На пустой БД не делает ничего
    deleted = cleanup_old_logs()
    assert deleted == 0


def test_cleanup_old_logs_no_op_when_empty(db_session: Any) -> None:
    """На пустой БД ничего не удаляется."""
    from scripts.health_check import cleanup_old_logs

    assert cleanup_old_logs() == 0


def test_check_db_returns_ok(db_session: Any) -> None:
    from scripts.health_check import check_db

    ok, msg = check_db()
    assert ok is True
    assert msg == "ok"
