"""scripts/status.py — CLI для просмотра состояния системы.

Запуск: `python scripts/status.py`

Показывает в одном окне:
- Канал и его конфиг
- Сколько статей увидено / каких качеств
- Сколько постов в очереди / опубликовано / отклонено
- Последний health-check
- Последние 5 ошибок из таблицы logs
- Размер БД и каталога data/

Полезно для оперативной диагностики без открытия БД руками.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import func, select  # noqa: E402

from bot.config import DATA_DIR, get_settings  # noqa: E402
from bot.db import db_path, init_db, session_scope  # noqa: E402
from bot.models import Article, LogEntry, Post  # noqa: E402
from bot.storage import ensure_channel, get_state  # noqa: E402


def _fmt_size(num_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes = int(num_bytes / 1024)
    return f"{num_bytes} TB"


def _bar(title: str) -> None:
    print(f"\n{'─' * 70}\n{title}\n{'─' * 70}")


def main() -> None:
    settings = get_settings()
    init_db()

    print(f"\n{'═' * 70}")
    print("  📊 Status — Neuro-News Bot")
    print(f"{'═' * 70}")

    # ─── Канал ──────────────────────────────────────────────────────
    _bar("📡 Канал")
    print(f"  Topic:       {settings.channel_topic}")
    print(f"  Niche:       {settings.channel_niche}")
    print(f"  Language:    {settings.channel_lang}")
    print(f"  RSS feeds:   {len(settings.rss_feeds)}")
    print(f"  Telegram:    {settings.telegram_channel_id}")
    print(f"  Moderator:   {settings.telegram_moderator_id}")

    # ─── БД ─────────────────────────────────────────────────────────
    _bar("💾 База данных")
    p = db_path()
    if p and p.exists():
        print(f"  Файл:        {p}")
        print(f"  Размер:      {_fmt_size(p.stat().st_size)}")
    else:
        print(f"  URL:         {settings.db_url}")

    with session_scope() as session:
        channel = ensure_channel(session)

        # ─── Статьи ─────────────────────────────────────────────────
        _bar("📰 Статьи")
        total_articles = (
            session.execute(
                select(func.count()).select_from(Article).where(Article.channel_id == channel.id)
            ).scalar()
            or 0
        )
        print(f"  Всего:       {total_articles}")

        for quality in ("HIGH", "MEDIUM", "LOW"):
            n = (
                session.execute(
                    select(func.count())
                    .select_from(Article)
                    .where(Article.channel_id == channel.id, Article.quality == quality)
                ).scalar()
                or 0
            )
            print(f"  {quality:7s}    {n}")

        # ─── Посты ──────────────────────────────────────────────────
        _bar("📝 Посты")
        for status_name, label in [
            ("pending", "В очереди"),
            ("published", "Опубликовано"),
            ("rejected", "Отклонено"),
            ("failed", "Не удалось"),
        ]:
            n = (
                session.execute(
                    select(func.count())
                    .select_from(Post)
                    .where(Post.channel_id == channel.id, Post.status == status_name)
                ).scalar()
                or 0
            )
            print(f"  {label:13s} {n}")

        # ─── Pending: подробно ──────────────────────────────────────
        pending_posts = list(
            session.execute(
                select(Post)
                .where(Post.channel_id == channel.id, Post.status == "pending")
                .order_by(Post.created_at.desc())
                .limit(5)
            ).scalars()
        )
        if pending_posts:
            _bar("⏳ Последние pending-посты (до 5)")
            for post in pending_posts:
                age = ""
                if post.created_at:
                    from datetime import UTC, datetime

                    # SQLite возвращает naive datetime — нормализуем к UTC
                    created = post.created_at
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=UTC)
                    delta = datetime.now(UTC) - created
                    hours = int(delta.total_seconds() / 3600)
                    age = f"{hours}ч назад"
                print(f"  [{post.id}] msg_id={post.moderator_msg_id}  {age}")

        # ─── Health-check ───────────────────────────────────────────
        _bar("🩺 Последний health-check")
        last_at = get_state(session, "last_health_check_at") or "—"
        last_status = get_state(session, "last_health_check_status") or "—"
        print(f"  Время:       {last_at}")
        print(f"  Статус:      {last_status}")

        # ─── Последние ошибки ──────────────────────────────────────
        recent_errors = list(
            session.execute(
                select(LogEntry)
                .where(LogEntry.level.in_(("ERROR", "CRITICAL")))
                .order_by(LogEntry.created_at.desc())
                .limit(5)
            ).scalars()
        )
        if recent_errors:
            _bar("🚨 Последние 5 ошибок")
            for log in recent_errors:
                ts = log.created_at.strftime("%Y-%m-%d %H:%M") if log.created_at else "—"
                print(f"  {ts}  [{log.level}] {log.event}: {log.message[:60]}")

        # ─── Telegram offset ────────────────────────────────────────
        offset = get_state(session, "tg_offset")
        if offset:
            _bar("📬 Telegram update offset")
            print(f"  Offset:      {offset}")

    # ─── Каталог data ───────────────────────────────────────────────
    _bar("📁 Каталог data/")
    if DATA_DIR.exists():
        total = sum(f.stat().st_size for f in DATA_DIR.rglob("*") if f.is_file())
        files = sum(1 for f in DATA_DIR.rglob("*") if f.is_file())
        print(f"  Файлов:      {files}")
        print(f"  Размер:      {_fmt_size(total)}")

    print(f"\n{'═' * 70}\n")


if __name__ == "__main__":
    main()
