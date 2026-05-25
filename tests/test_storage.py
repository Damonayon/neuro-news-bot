"""Тесты bot.storage — операции над БД."""

from __future__ import annotations

from typing import Any

from bot.models import POST_STATUS_PENDING, POST_STATUS_PUBLISHED, Article
from bot.storage import (
    article_hash,
    create_pending_post,
    ensure_channel,
    get_pending_by_article_hash,
    get_state,
    known_article_hashes,
    mark_published,
    mark_rejected,
    save_article,
    set_state,
)


class TestArticleHash:
    def test_stable(self) -> None:
        h1 = article_hash("https://example.com/article")
        h2 = article_hash("https://example.com/article")
        assert h1 == h2
        assert len(h1) == 16

    def test_canonicalizes(self) -> None:
        """UTM-метки не должны менять hash."""
        h1 = article_hash("https://example.com/article?id=1")
        h2 = article_hash("https://example.com/article?id=1&utm_source=fb")
        assert h1 == h2

    def test_different_urls_different_hash(self) -> None:
        h1 = article_hash("https://example.com/a")
        h2 = article_hash("https://example.com/b")
        assert h1 != h2


class TestEnsureChannel:
    def test_creates_on_first_call(self, db_session: Any) -> None:
        channel = ensure_channel(db_session)
        assert channel.id is not None
        assert channel.topic == "Test Channel"
        assert channel.slug
        assert channel.language == "русский"

    def test_idempotent(self, db_session: Any) -> None:
        ch1 = ensure_channel(db_session)
        ch2 = ensure_channel(db_session)
        assert ch1.id == ch2.id


class TestSaveArticle:
    def test_creates(self, db_session: Any) -> None:
        ch = ensure_channel(db_session)
        art = save_article(
            db_session,
            channel_id=ch.id,
            url="https://x.com/1",
            title="Title 1",
            summary="Summary 1",
            quality="HIGH",
        )
        assert art.id is not None
        assert art.quality == "HIGH"

    def test_idempotent_by_url(self, db_session: Any) -> None:
        """Сохранение той же статьи дважды — без дубликатов."""
        ch = ensure_channel(db_session)
        a1 = save_article(
            db_session, channel_id=ch.id, url="https://x.com/1", title="t", summary="s"
        )
        a2 = save_article(
            db_session, channel_id=ch.id, url="https://x.com/1", title="t", summary="s"
        )
        assert a1.id == a2.id

    def test_utm_doesnt_create_duplicate(self, db_session: Any) -> None:
        """Та же статья с UTM — не должна создать вторую запись."""
        ch = ensure_channel(db_session)
        save_article(
            db_session, channel_id=ch.id, url="https://x.com/1?utm_source=a", title="t", summary="s"
        )
        save_article(
            db_session, channel_id=ch.id, url="https://x.com/1?utm_source=b", title="t", summary="s"
        )
        count = db_session.query(Article).filter(Article.channel_id == ch.id).count()
        assert count == 1


class TestKnownHashes:
    def test_returns_all(self, db_session: Any) -> None:
        ch = ensure_channel(db_session)
        save_article(db_session, channel_id=ch.id, url="https://x.com/1", title="t", summary="")
        save_article(db_session, channel_id=ch.id, url="https://x.com/2", title="t", summary="")
        hashes = known_article_hashes(db_session, ch.id)
        assert len(hashes) == 2
        assert article_hash("https://x.com/1") in hashes


class TestPostLifecycle:
    def test_full_lifecycle(self, db_session: Any) -> None:
        ch = ensure_channel(db_session)
        art = save_article(
            db_session, channel_id=ch.id, url="https://x.com/1", title="t", summary=""
        )

        # 1. pending
        post = create_pending_post(
            db_session,
            article=art,
            channel_id=ch.id,
            post_text="hello",
            image_url="https://img/1",
            image_prompt="cat",
            image_file_id="file_xyz",
            moderator_msg_id=42,
            model_used="gpt-4o",
        )
        assert post.status == POST_STATUS_PENDING
        assert post.image_file_id == "file_xyz"

        # 2. get_pending_by_article_hash возвращает его
        found = get_pending_by_article_hash(db_session, ch.id, art.article_hash)
        assert found is not None
        assert found.id == post.id

        # 3. mark_published
        mark_published(db_session, post)
        assert post.status == POST_STATUS_PUBLISHED
        assert post.decided_at is not None
        assert post.published_at is not None

        # 4. После публикации в pending уже нет
        found = get_pending_by_article_hash(db_session, ch.id, art.article_hash)
        assert found is None

    def test_reject(self, db_session: Any) -> None:
        ch = ensure_channel(db_session)
        art = save_article(
            db_session, channel_id=ch.id, url="https://x.com/1", title="t", summary=""
        )
        post = create_pending_post(
            db_session,
            article=art,
            channel_id=ch.id,
            post_text="hi",
            image_url=None,
            image_prompt=None,
            moderator_msg_id=1,
        )
        mark_rejected(db_session, post)
        assert post.status == "rejected"
        assert post.decided_at is not None


class TestSystemState:
    def test_set_and_get(self, db_session: Any) -> None:
        assert get_state(db_session, "missing") is None
        set_state(db_session, "x", "value1")
        assert get_state(db_session, "x") == "value1"
        # Update
        set_state(db_session, "x", "value2")
        assert get_state(db_session, "x") == "value2"
