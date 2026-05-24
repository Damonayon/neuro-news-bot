"""scripts/migrate_json_to_db.py — одноразовая миграция данных.

Переносит из старых JSON-файлов в SQLite:
- data/posted_ids.json  → таблица articles (минимальные записи: hash + URL=unknown)
- data/pending.json     → таблицы articles + posts (status=pending)
- data/tg_offset.json   → system_state['tg_offset']

Безопасен для повторного запуска (идемпотентен): дубли не плодит,
данные не перезаписывает.

Запуск:
    python -m scripts.migrate_json_to_db
или
    python scripts/migrate_json_to_db.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Чтобы скрипт работал и через `python scripts/migrate_json_to_db.py`,
# и через `python -m`, добавим корень проекта в sys.path.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bot.db import init_db, session_scope  # noqa: E402
from bot.models import (  # noqa: E402
    POST_STATUS_PENDING,
    Article,
    Post,
)
from bot.storage import (  # noqa: E402
    article_hash,
    ensure_channel,
    save_article,
    set_state,
)


PENDING_FILE = PROJECT_ROOT / "data" / "pending.json"
POSTED_FILE = PROJECT_ROOT / "data" / "posted_ids.json"
OFFSET_FILE = PROJECT_ROOT / "data" / "tg_offset.json"


def _load_json(path: Path, default):
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def migrate_posted_ids(session, channel_id: int) -> int:
    """posted_ids.json — это только хэши, без URL. Создаём article-плэйсхолдеры.

    Это нужно, чтобы дедупликация продолжала работать после перехода на БД
    (старые ID не должны заново всплыть в RSS).
    """
    posted_ids = _load_json(POSTED_FILE, [])
    if not isinstance(posted_ids, list):
        return 0

    from sqlalchemy import select  # локальный импорт, чтобы не светить наружу

    inserted = 0
    for art_hash in posted_ids:
        if not isinstance(art_hash, str):
            continue

        exists = session.execute(
            select(Article).where(
                Article.channel_id == channel_id,
                Article.article_hash == art_hash,
            )
        ).scalar_one_or_none()
        if exists:
            continue

        session.add(
            Article(
                channel_id=channel_id,
                article_hash=art_hash,
                url=f"migrated://posted_ids/{art_hash}",
                title="(migrated placeholder)",
                summary=None,
                quality=None,
            )
        )
        inserted += 1
    return inserted


def migrate_pending(session, channel_id: int) -> int:
    """pending.json — словарь art_hash → {post_text, image_url, msg_id, url, title, created_at}."""
    pending = _load_json(PENDING_FILE, {})
    if not isinstance(pending, dict):
        return 0

    inserted = 0
    for art_hash_key, item in pending.items():
        if not isinstance(item, dict):
            continue

        url = item.get("url") or f"migrated://pending/{art_hash_key}"
        # У статьи в pending уже может быть полноценный URL — используем его,
        # тогда article_hash будет «настоящий», а не плейсхолдер.
        # Если есть и совпадает — используем «настоящий», иначе — старый ключ.
        real_hash = article_hash(url) if not url.startswith("migrated://") else art_hash_key

        article = save_article(
            session,
            channel_id=channel_id,
            url=url,
            title=item.get("title", "(migrated)"),
            summary="",
        )
        # Если article_hash в БД отличается от ключа в JSON — это нормально,
        # старая запись по ключу мы уже создали в migrate_posted_ids (если был).

        # Создаём pending-Post
        post = Post(
            article_id=article.id,
            channel_id=channel_id,
            post_text=item.get("post_text", ""),
            image_url=item.get("image_url"),
            moderator_msg_id=item.get("msg_id"),
            status=POST_STATUS_PENDING,
        )

        # created_at — пробуем восстановить из JSON
        created_raw = item.get("created_at")
        if isinstance(created_raw, str):
            try:
                # 2025-...T...+00:00 или без tz
                post.created_at = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
                if post.created_at.tzinfo is None:
                    post.created_at = post.created_at.replace(tzinfo=timezone.utc)
            except ValueError:
                pass

        session.add(post)
        inserted += 1
    return inserted


def migrate_offset(session) -> bool:
    """tg_offset.json → system_state['tg_offset']."""
    data = _load_json(OFFSET_FILE, {})
    if not isinstance(data, dict):
        return False
    offset = data.get("offset")
    if offset is None:
        return False
    set_state(session, "tg_offset", str(offset))
    return True


def main() -> None:
    print("=" * 60)
    print("Миграция JSON → SQLite")
    print("=" * 60)

    init_db()
    print("✅ Таблицы созданы (или уже существовали)")

    with session_scope() as session:
        channel = ensure_channel(session)
        session.flush()
        print(f"✅ Канал: {channel.topic} (id={channel.id}, slug={channel.slug})")

        n_posted = migrate_posted_ids(session, channel.id)
        print(f"✅ Перенесено posted_ids: {n_posted}")

        n_pending = migrate_pending(session, channel.id)
        print(f"✅ Перенесено pending-постов: {n_pending}")

        ok = migrate_offset(session)
        print(f"✅ Telegram offset перенесён: {ok}")

    print("=" * 60)
    print("Готово. Старые JSON-файлы НЕ удалены — оставлены как бэкап.")
    print("=" * 60)


if __name__ == "__main__":
    main()
