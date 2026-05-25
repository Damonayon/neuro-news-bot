"""bot.storage — высокоуровневые операции над БД.

Скрипты НЕ работают с ORM напрямую — они вызывают функции этого модуля.
Так замена движка БД (SQLite → Supabase в T3.3) пройдёт без правки логики.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, TypeVar, cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from bot.config import get_settings
from bot.db import session_scope
from bot.models import (
    POST_STATUS_PENDING,
    POST_STATUS_PUBLISHED,
    POST_STATUS_REJECTED,
    Article,
    Channel,
    Post,
    SystemState,
)
from bot.utils import canonicalize_url

# ─── channels ────────────────────────────────────────────────────────────────


def ensure_channel(session: Session) -> Channel:
    """Возвращает Channel из БД, создавая запись из текущего env-конфига при первом запуске."""
    settings = get_settings()
    slug = settings.channel_slug

    channel = session.execute(select(Channel).where(Channel.slug == slug)).scalar_one_or_none()

    if channel is None:
        channel = Channel(
            slug=slug,
            topic=settings.channel_topic,
            niche=settings.channel_niche,
            audience=settings.channel_audience,
            language=settings.channel_lang,
            telegram_channel_id=settings.telegram_channel_id,
        )
        session.add(channel)
        session.flush()
    else:
        # Обновляем поля если изменились (env — источник правды для конфига)
        channel.topic = settings.channel_topic
        channel.niche = settings.channel_niche
        channel.audience = settings.channel_audience
        channel.language = settings.channel_lang
        channel.telegram_channel_id = settings.telegram_channel_id

    return channel


# ─── articles ────────────────────────────────────────────────────────────────


def article_hash(url: str) -> str:
    """Стабильный 16-символьный ID статьи.

    Считается от КАНОНИЧЕСКОЙ формы URL: одна и та же статья,
    пришедшая с разными UTM-метками, даст один хэш и не задвоится.
    """
    canonical = canonicalize_url(url)
    return hashlib.md5(canonical.encode("utf-8")).hexdigest()[:16]


def known_article_hashes(session: Session, channel_id: int) -> set[str]:
    """Множество всех ранее увиденных hash для канала.
    Заменяет старый `posted_ids.json`."""
    rows = session.execute(
        select(Article.article_hash).where(Article.channel_id == channel_id)
    ).scalars()
    return set(rows)


def save_article(
    session: Session,
    *,
    channel_id: int,
    url: str,
    title: str,
    summary: str,
    source_feed: str | None = None,
    quality: str | None = None,
    quality_reason: str | None = None,
    rubric: str | None = None,
) -> Article:
    """Сохраняет статью (upsert по channel_id + article_hash)."""
    h = article_hash(url)
    existing = session.execute(
        select(Article).where(Article.channel_id == channel_id, Article.article_hash == h)
    ).scalar_one_or_none()

    if existing is not None:
        # Обогащаем если появились новые сведения
        if quality is not None:
            existing.quality = quality
            existing.quality_reason = quality_reason
        if rubric is not None:
            existing.rubric = rubric
        return existing

    art = Article(
        channel_id=channel_id,
        article_hash=h,
        url=url,
        title=title,
        summary=summary,
        source_feed=source_feed,
        quality=quality,
        quality_reason=quality_reason,
        rubric=rubric,
    )
    session.add(art)
    session.flush()
    return art


# ─── posts ───────────────────────────────────────────────────────────────────


def create_pending_post(
    session: Session,
    *,
    article: Article,
    channel_id: int,
    post_text: str,
    image_url: str | None,
    image_prompt: str | None,
    moderator_msg_id: int,
    image_file_id: str | None = None,
    model_used: str | None = None,
) -> Post:
    """Создаёт пост в статусе pending (отправлен модератору)."""
    post = Post(
        article_id=article.id,
        channel_id=channel_id,
        post_text=post_text,
        image_url=image_url,
        image_prompt=image_prompt,
        image_file_id=image_file_id,
        moderator_msg_id=moderator_msg_id,
        model_used=model_used,
        status=POST_STATUS_PENDING,
    )
    session.add(post)
    session.flush()
    return post


def get_pending_by_article_hash(session: Session, channel_id: int, art_hash: str) -> Post | None:
    """Достаёт pending-пост по хэшу статьи (то, чем были callback_data в Telegram)."""
    return session.execute(
        select(Post)
        .join(Article, Post.article_id == Article.id)
        .where(
            Article.channel_id == channel_id,
            Article.article_hash == art_hash,
            Post.status == POST_STATUS_PENDING,
        )
    ).scalar_one_or_none()


def mark_published(session: Session, post: Post) -> None:
    now = datetime.now(UTC)
    post.status = POST_STATUS_PUBLISHED
    post.decided_at = now
    post.published_at = now


def mark_rejected(session: Session, post: Post) -> None:
    post.status = POST_STATUS_REJECTED
    post.decided_at = datetime.now(UTC)


# ─── system_state ────────────────────────────────────────────────────────────


def get_state(session: Session, key: str) -> str | None:
    row = session.get(SystemState, key)
    return row.value if row else None


def set_state(session: Session, key: str, value: str) -> None:
    """Upsert ключ-значение."""
    row = session.get(SystemState, key)
    if row is None:
        session.add(SystemState(key=key, value=value))
    else:
        row.value = value


# ─── удобные обёртки ─────────────────────────────────────────────────────────


F = TypeVar("F", bound=Callable[..., Any])


def with_session(func: F) -> F:
    """Декоратор: открывает session_scope и передаёт session первым аргументом."""

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        with session_scope() as session:
            return func(session, *args, **kwargs)

    return cast(F, wrapper)
