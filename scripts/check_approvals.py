"""check_approvals.py — публикация одобренных постов в канал (версия с БД).

Получает callback-нажатия из Telegram, при ✅ публикует пост в канал,
при ❌ помечает отклонённым. Состояние очереди — в SQLite (см. bot/storage.py).
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bot.config import get_settings  # noqa: E402
from bot.db import init_db, session_scope  # noqa: E402
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


# ─── Telegram-обёртки ────────────────────────────────────────────────────────


def tg(method: str, payload: dict) -> dict:
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}",
            json=payload,
            timeout=15,
        )
        return resp.json()
    except requests.RequestException as exc:
        return {"ok": False, "description": f"network: {exc}"}


def notify_moderator(text: str) -> None:
    tg("sendMessage", {"chat_id": settings.telegram_moderator_id, "text": text})


def get_updates(offset: int | None) -> list[dict]:
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


def publish_to_channel(post_text: str, image_url: str | None) -> bool:
    if image_url and len(post_text) <= 1024:
        result = tg(
            "sendPhoto",
            {
                "chat_id": settings.telegram_channel_id,
                "photo": image_url,
                "caption": post_text,
                "parse_mode": "HTML",
            },
        )
        if result.get("ok"):
            return True
        print(f"Фото не отправилось: {result.get('description')}")

    if image_url:
        tg("sendPhoto", {"chat_id": settings.telegram_channel_id, "photo": image_url})

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
    print(
        f"\nПроверка одобрений [{settings.channel_topic}] — "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M')}"
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
        print(f"Обновлений: {len(updates)}")

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

        print("Готово\n")

    except Exception as exc:
        msg = (
            f"❌ Ошибка check_approvals [{settings.channel_topic}]:\n"
            f"{type(exc).__name__}: {exc}"
        )
        print(msg)
        notify_moderator(msg)
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
        post_id = post.id

    # 2) Сетевой вызов БЕЗ открытой транзакции
    success = publish_to_channel(post_text, image_url)

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
        print(f"✅ Опубликован: post_id={post_id}")
    else:
        answer_callback(cq_id, "❌ Ошибка публикации")
        notify_moderator(f"❌ Не удалось опубликовать [{settings.channel_topic}]")


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
    print(f"❌ Отклонён: post_id={post_id}")


if __name__ == "__main__":
    main()
