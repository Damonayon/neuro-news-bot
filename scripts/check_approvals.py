"""check_approvals.py — публикация одобренных постов в канал (версия с БД).

Получает callback-нажатия из Telegram, при ✅ публикует пост в канал,
при ❌ помечает отклонённым. Состояние очереди — в SQLite (см. bot/storage.py).
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bot.config import get_settings  # noqa: E402
from bot.db import init_db, session_scope  # noqa: E402
from bot.http import (  # noqa: E402
    CircuitOpenError,
    DeadlineExceededError,
    http_post,
    set_deadline,
)
from bot.logging_setup import get_logger, setup_logging  # noqa: E402
from bot.models import Post  # noqa: E402
from bot.storage import (  # noqa: E402
    ensure_channel,
    get_pending_by_article_hash,
    get_state,
    mark_published,
    mark_rejected,
    set_state,
)

settings = get_settings()
log = get_logger("check_approvals")

# Этот скрипт короткий (cron каждые 10 мин) — даём ему 2 минуты на всё.
PROCESS_DEADLINE_SEC = 120


# ─── Telegram-обёртки ────────────────────────────────────────────────────────


def tg(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        resp = http_post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}",
            json=payload,
            timeout=15,
        )
        data: dict[str, Any] = resp.json()
        return data
    except (requests.RequestException, CircuitOpenError, DeadlineExceededError) as exc:
        return {"ok": False, "description": f"network: {exc}"}


def notify_moderator(text: str) -> None:
    tg("sendMessage", {"chat_id": settings.telegram_moderator_id, "text": text})


def get_updates(offset: int | None) -> list[dict[str, Any]]:
    payload = {"limit": 100, "timeout": 0}
    if offset is not None:
        payload["offset"] = offset
    result = tg("getUpdates", payload)
    return result.get("result", []) if result.get("ok") else []


def answer_callback(cq_id: str, text: str) -> None:
    tg(
        "answerCallbackQuery",
        {"callback_query_id": cq_id, "text": text, "show_alert": False},
    )


def publish_to_channel(
    post_text: str,
    image_file_id: str | None,
    image_url: str | None,
) -> bool:
    """Публикует пост в канал.

    Источник картинки в порядке предпочтения:
    1. image_file_id — Telegram-нативный, не зависит от внешних сервисов.
    2. image_url — fallback (например, для старых постов без file_id).

    Если фото не помещается в caption (>1024) — шлём отдельно картинку и текст.
    """
    photo_source = image_file_id or image_url

    if photo_source and len(post_text) <= 1024:
        result = tg(
            "sendPhoto",
            {
                "chat_id": settings.telegram_channel_id,
                "photo": photo_source,
                "caption": post_text,
                "parse_mode": "HTML",
            },
        )
        if result.get("ok"):
            return True
        log.warning("Фото не отправилось: %s", result.get("description"))

    if photo_source:
        tg(
            "sendPhoto",
            {"chat_id": settings.telegram_channel_id, "photo": photo_source},
        )

    result = tg(
        "sendMessage",
        {
            "chat_id": settings.telegram_channel_id,
            "text": post_text[:4096],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
    )
    return bool(result.get("ok", False))


def remove_buttons(msg_id: int, status_label: str) -> None:
    for method in ("editMessageCaption", "editMessageText"):
        field = "caption" if "Caption" in method else "text"
        result = tg(
            method,
            {
                "chat_id": settings.telegram_moderator_id,
                "message_id": msg_id,
                field: status_label,
                "reply_markup": {"inline_keyboard": []},
            },
        )
        if result.get("ok"):
            break


# ─── Основной цикл ───────────────────────────────────────────────────────────


def main() -> None:
    setup_logging()
    set_deadline(PROCESS_DEADLINE_SEC)
    log.info(
        "=== Проверка одобрений [%s] — %s ===",
        settings.channel_topic,
        datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    init_db()

    try:
        # Достаём offset и channel_id одной сессией
        with session_scope() as session:
            channel = ensure_channel(session)
            channel_id = channel.id
            offset_raw = get_state(session, "tg_offset")

        offset = int(offset_raw) if offset_raw is not None else None
        updates = get_updates(offset)
        log.info("Обновлений: %d", len(updates))

        new_offset = offset

        for update in updates:
            new_offset = update["update_id"] + 1

            if "callback_query" not in update:
                continue

            cq = update["callback_query"]
            data = cq.get("data", "")
            cq_id = cq["id"]
            msg_id = cq["message"]["message_id"]

            if data.startswith("approve_"):
                _handle_approve(channel_id, data.removeprefix("approve_"), cq_id, msg_id)
            elif data.startswith("reject_"):
                _handle_reject(channel_id, data.removeprefix("reject_"), cq_id, msg_id)

        # Сохраняем offset даже если изменений нет — отдельной короткой транзакцией
        if new_offset is not None and new_offset != offset:
            with session_scope() as session:
                set_state(session, "tg_offset", str(new_offset))

        log.info("Готово")

    except Exception as exc:
        log.exception("Сбой check_approvals [%s]: %s", settings.channel_topic, type(exc).__name__)
        notify_moderator(
            f"❌ Сбой check_approvals [{settings.channel_topic}]: {type(exc).__name__}: {exc}"
        )
        raise


def _handle_approve(channel_id: int, art_hash: str, cq_id: str, msg_id: int) -> None:
    # 1) Транзакция: достаём pending-пост и фиксируем намерение
    with session_scope() as session:
        post = get_pending_by_article_hash(session, channel_id, art_hash)
        if post is None:
            answer_callback(cq_id, "⚠️ Уже обработан")
            return
        post_text = post.post_text
        image_url = post.image_url
        image_file_id = post.image_file_id
        post_id = post.id

    # 2) Сетевой вызов БЕЗ открытой транзакции
    success = publish_to_channel(post_text, image_file_id, image_url)

    # 3) Фиксируем результат
    with session_scope() as session:
        post = session.get(Post, post_id)
        if post is None:
            return
        if success:
            mark_published(session, post)

    if success:
        answer_callback(cq_id, "✅ Опубликовано!")
        remove_buttons(msg_id, f"✅ ОПУБЛИКОВАНО [{settings.channel_topic}]")
        log.info("Опубликован post_id=%d", post_id)
    else:
        answer_callback(cq_id, "❌ Ошибка публикации")
        log.error("Не удалось опубликовать post_id=%d", post_id)


def _handle_reject(channel_id: int, art_hash: str, cq_id: str, msg_id: int) -> None:
    with session_scope() as session:
        post = get_pending_by_article_hash(session, channel_id, art_hash)
        if post is None:
            answer_callback(cq_id, "⚠️ Уже обработан")
            return
        mark_rejected(session, post)
        post_id = post.id

    answer_callback(cq_id, "❌ Отклонено")
    remove_buttons(msg_id, f"❌ ОТКЛОНЕНО [{settings.channel_topic}]")
    log.info("Отклонён post_id=%d", post_id)


if __name__ == "__main__":
    main()
