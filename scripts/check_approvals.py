"""
check_approvals.py — ФИНАЛЬНАЯ ВЕРСИЯ
Обрабатывает кнопки и публикует посты в канал с HTML-форматированием.
"""

import os, json, requests
from datetime import datetime

BOT_TOKEN    = os.environ["TELEGRAM_BOT_TOKEN"]
MODERATOR_ID = os.environ["TELEGRAM_MODERATOR_ID"]
CHANNEL_ID   = os.environ["TELEGRAM_CHANNEL_ID"]

DATA_DIR     = "data"
PENDING_FILE = f"{DATA_DIR}/pending.json"
OFFSET_FILE  = f"{DATA_DIR}/tg_offset.json"


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

def save_json(path, data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def tg(method, payload):
    resp = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        json=payload,
        timeout=15,
    )
    return resp.json()

def notify_moderator(text):
    try:
        tg("sendMessage", {"chat_id": MODERATOR_ID, "text": text})
    except Exception:
        pass

def get_updates(offset):
    result = tg("getUpdates", {"offset": offset, "limit": 100, "timeout": 0})
    if not result.get("ok"):
        print(f"getUpdates ошибка: {result}")
        return []
    return result.get("result", [])

def answer_callback(cq_id, text):
    tg("answerCallbackQuery", {"callback_query_id": cq_id, "text": text, "show_alert": False})

def publish_to_channel(post_text, image_url):
    """
    Публикует пост в канал с HTML-форматированием и картинкой.
    """
    # С картинкой
    if image_url:
        result = tg("sendPhoto", {
            "chat_id":    CHANNEL_ID,
            "photo":      image_url,
            "caption":    post_text[:1024],
            "parse_mode": "HTML",
        })
        if result.get("ok"):
            return True
        print(f"Фото не отправилось: {result.get('description')}, пробую текстом...")

    # Без картинки — только текст с HTML
    result = tg("sendMessage", {
        "chat_id":                  CHANNEL_ID,
        "text":                     post_text[:4096],
        "parse_mode":               "HTML",
        "disable_web_page_preview": False,
    })
    return result.get("ok", False)

def update_moderator_message(msg_id, status_text):
    """Убирает кнопки с сообщения модератора после обработки."""
    for method in ("editMessageCaption", "editMessageText"):
        field = "caption" if method == "editMessageCaption" else "text"
        result = tg(method, {
            "chat_id":    MODERATOR_ID,
            "message_id": msg_id,
            field:        status_text[:1024],
            "reply_markup": {"inline_keyboard": []},
        })
        if result.get("ok"):
            return


def main():
    print(f"\nПроверка одобрений — {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    try:
        pending     = load_json(PENDING_FILE, {})
        offset_data = load_json(OFFSET_FILE, {"offset": None})
        offset      = offset_data.get("offset")

        updates = get_updates(offset)
        print(f"Обновлений: {len(updates)}")

        new_offset = offset
        changed    = False

        for update in updates:
            new_offset = update["update_id"] + 1

            if "callback_query" not in update:
                continue

            cq     = update["callback_query"]
            data   = cq.get("data", "")
            cq_id  = cq["id"]
            msg_id = cq["message"]["message_id"]

            # ── ✅ ОДОБРЕНИЕ ──────────────────────────────────────────────────
            if data.startswith("approve_"):
                art_id = data.removeprefix("approve_")

                if art_id not in pending:
                    answer_callback(cq_id, "⚠️ Этот пост уже обработан")
                    continue

                item      = pending[art_id]
                post_text = item["post_text"]
                image_url = item.get("image_url", "")

                success = publish_to_channel(post_text, image_url)

                if success:
                    answer_callback(cq_id, "✅ Пост опубликован в канале!")
                    update_moderator_message(msg_id, "✅ ОПУБЛИКОВАНО")
                    del pending[art_id]
                    changed = True
                    print(f"✅ Опубликован: {art_id}")
                else:
                    answer_callback(cq_id, "❌ Ошибка публикации — попробуй ещё раз")
                    notify_moderator(f"❌ Не удалось опубликовать пост {art_id}")

            # ── ❌ ОТКЛОНЕНИЕ ─────────────────────────────────────────────────
            elif data.startswith("reject_"):
                art_id = data.removeprefix("reject_")

                if art_id not in pending:
                    answer_callback(cq_id, "⚠️ Этот пост уже обработан")
                    continue

                answer_callback(cq_id, "❌ Пост отклонён")
                update_moderator_message(msg_id, "❌ ОТКЛОНЕНО")
                del pending[art_id]
                changed = True
                print(f"❌ Отклонён: {art_id}")

        if changed:
            save_json(PENDING_FILE, pending)

        save_json(OFFSET_FILE, {"offset": new_offset})
        print("Проверка завершена\n")

    except Exception as e:
        error_msg = f"❌ Ошибка check_approvals:\n{type(e).__name__}: {e}"
        print(error_msg)
        notify_moderator(error_msg)
        raise


if __name__ == "__main__":
    main()
