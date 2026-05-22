"""
generate_post.py — ФИНАЛЬНАЯ ПРОФЕССИОНАЛЬНАЯ ВЕРСИЯ
"""

import os, json, time, random, hashlib, urllib.parse, re
import requests, feedparser
from datetime import datetime, timezone

BOT_TOKEN      = os.environ["TELEGRAM_BOT_TOKEN"]
MODERATOR_ID   = os.environ["TELEGRAM_MODERATOR_ID"]
CHANNEL_ID     = os.environ["TELEGRAM_CHANNEL_ID"]
OPENROUTER_KEY = os.environ["OPENROUTER_KEY"]

RSS_FEEDS = [
    "https://habr.com/ru/rss/hub/artificial_intelligence/all/",
    "https://habr.com/ru/rss/hub/machine_learning/all/",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://venturebeat.com/category/ai/feed/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://openai.com/blog/rss/",
    "https://blogs.nvidia.com/feed/",
]

DATA_DIR     = "data"
PENDING_FILE = f"{DATA_DIR}/pending.json"
POSTED_FILE  = f"{DATA_DIR}/posted_ids.json"


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

def article_id(url):
    return hashlib.md5(url.encode()).hexdigest()[:16]

def notify_moderator(text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": MODERATOR_ID, "text": text},
            timeout=10,
        )
    except Exception:
        pass

def fetch_articles():
    articles = []
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:5]:
                url = entry.get("link", "")
                if not url:
                    continue
                articles.append({
                    "id":      article_id(url),
                    "title":   entry.get("title", "").strip(),
                    "url":     url,
                    "summary": entry.get("summary", "")[:800].strip(),
                })
        except Exception as e:
            print(f"Ошибка загрузки {feed_url}: {e}")
    print(f"Статей из RSS: {len(articles)}")
    return articles


PROMPT_TEMPLATE = """ВАЖНО: Весь текст поста пиши ТОЛЬКО на русском языке. Никакого английского в тексте поста.

Ты — топ-редактор Telegram-канала «Нейро-новости». 500 000 подписчиков. Русскоязычная аудитория.

НОВОСТЬ:
Заголовок: {title}
Содержание: {summary}
Ссылка: {url}

Верни СТРОГО JSON, ничего кроме JSON:
{{"post": "текст поста на русском в Telegram HTML", "image_prompt": "visual description in english"}}

═══ СТРУКТУРА ПОСТА (строго) ═══

[СТРОКА 1 — КРЮЧОК]
Одна строка. Останавливает скролл. Варианты:
• Шокирующий факт: "ИИ заменил 300 юристов за одну ночь 🔻"
• Провокация: "Твоя профессия устареет через 2 года. Проверь себя 👇"  
• Интрига: "Google скрывал это 6 месяцев. Теперь всё изменится 🤫"

[ПУСТАЯ СТРОКА]

[2-3 ПРЕДЛОЖЕНИЯ — СУТЬ]
Что случилось, простым языком. Цифры и факты. Никакого жаргона.

[ПУСТАЯ СТРОКА]

[2-3 ПРЕДЛОЖЕНИЯ — ЗАЧЕМ ЭТО ТЕБЕ]
Как это влияет на жизнь читателя конкретно.
"Если ты фрилансер — это значит...", "Для малого бизнеса..."

[ПУСТАЯ СТРОКА]

[1 СТРОКА — ВОПРОС ДЛЯ ОБСУЖДЕНИЯ]
Дискуссионный вопрос или острый тезис. Мотивирует написать комментарий.

[ПУСТАЯ СТРОКА]

[ХЕШТЕГИ] #ИИ #нейросети #тематический_хештег

[ПУСТАЯ СТРОКА]

<a href="{url}">📖 Читать полностью</a>

═══ ФОРМАТИРОВАНИЕ ═══
• <b>жирный</b> — 2-3 раза для ключевых слов
• <i>курсив</i> — для важных акцентов
• Эмодзи: 4-6 штук, только уместные
• Длина: 180-260 слов
• Запрещено: "революция", "прорыв", "невероятный", "уникальный"
• Стиль: умный друг + чуть дерзкий, как лучшие русские tech-блогеры

═══ IMAGE PROMPT (английский, max 120 символов) ═══
• Концептуальная иллюстрация к теме
• Стиль: cinematic concept art, dramatic lighting, 8k, no text, no faces
• Пример: "glowing AI microchip dark background neon blue light cinematic 8k"

Верни ТОЛЬКО JSON."""


def call_ai(prompt):
    last_error = None
    for attempt in range(5):
        try:
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_KEY}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":       "openrouter/free",
                    "messages":    [{"role": "user", "content": prompt}],
                    "max_tokens":  1500,
                    "temperature": 0.8,
                },
                timeout=60,
            )
            if response.status_code == 200:
                return response.json()["choices"][0]["message"]["content"].strip()
            elif response.status_code == 429:
                wait = 20 + attempt * 10
                print(f"Попытка {attempt+1}: rate limit, жду {wait}с...")
                time.sleep(wait)
                last_error = "429 rate limit"
            else:
                last_error = f"{response.status_code}: {response.text[:200]}"
                time.sleep(5)
        except Exception as e:
            last_error = str(e)
            time.sleep(5)
    raise RuntimeError(f"Все попытки исчерпаны: {last_error}")


def generate_content(article):
    prompt = PROMPT_TEMPLATE.format(**article)
    raw = call_ai(prompt)

    try:
        clean = raw
        if "```" in clean:
            parts = clean.split("```")
            clean = parts[1] if len(parts) > 1 else parts[0]
            if clean.startswith("json"):
                clean = clean[4:]
        data         = json.loads(clean.strip())
        post_text    = data.get("post", "").strip()
        image_prompt = data.get("image_prompt", "AI neural network cinematic dark neon 8k").strip()
        if not post_text:
            raise ValueError("Пустой пост")
        return post_text, image_prompt
    except Exception as e:
        print(f"JSON parse error: {e}")
        return raw.strip(), "AI technology neural network cinematic dark 8k"


def build_image_url(image_prompt):
    """Квадратное изображение 1080x1080 — идеально для Telegram"""
    seed    = random.randint(10000, 99999)
    prompt  = image_prompt + ", no text, no watermark, no faces, professional"
    encoded = urllib.parse.quote(prompt)
    return (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width=1080&height=1080&model=flux&nologo=true&enhance=true&seed={seed}"
    )


def send_for_approval(post_text, image_url, art_id):
    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ Опубликовать", "callback_data": f"approve_{art_id}"},
            {"text": "❌ Отклонить",    "callback_data": f"reject_{art_id}"},
        ]]
    }

    clean_preview = re.sub(r'<[^>]+>', '', post_text)
    caption = f"📬 Новый пост на одобрение:\n\n{clean_preview}"

    result = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
        json={
            "chat_id":      MODERATOR_ID,
            "photo":        image_url,
            "caption":      caption[:1024],
            "reply_markup": keyboard,
        },
        timeout=25,
    ).json()

    if not result.get("ok"):
        print(f"Фото не загрузилось, отправляю текстом...")
        result = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id":      MODERATOR_ID,
                "text":         caption[:4096],
                "reply_markup": keyboard,
            },
            timeout=10,
        ).json()

    if not result.get("ok"):
        raise RuntimeError(f"Не удалось отправить: {result}")

    return result["result"]["message_id"]


def main():
    print(f"\nЗапуск генерации — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    try:
        posted_ids   = load_json(POSTED_FILE, [])
        pending      = load_json(PENDING_FILE, {})
        articles     = fetch_articles()
        new_articles = [a for a in articles if a["id"] not in posted_ids]
        print(f"Новых статей: {len(new_articles)}")

        if not new_articles:
            print("Новых статей нет.")
            return

        article = new_articles[0]
        print(f"Обрабатываем: {article['title'][:80]}")

        post_text, image_prompt = generate_content(article)
        print(f"Пост готов: {len(post_text)} символов")

        image_url = build_image_url(image_prompt)
        msg_id    = send_for_approval(post_text, image_url, article["id"])
        print(f"Отправлено модератору, msg_id={msg_id}")

        pending[article["id"]] = {
            "post_text":       post_text,
            "image_url":       image_url,
            "article_title":   article["title"],
            "article_url":     article["url"],
            "telegram_msg_id": msg_id,
            "created_at":      datetime.now(timezone.utc).isoformat(),
        }
        posted_ids.append(article["id"])
        posted_ids = posted_ids[-500:]
        save_json(PENDING_FILE, pending)
        save_json(POSTED_FILE,  posted_ids)
        print("Готово!\n")

    except Exception as e:
        error_msg = f"❌ Ошибка генерации:\n{type(e).__name__}: {e}"
        print(error_msg)
        notify_moderator(error_msg)
        raise

if __name__ == "__main__":
    main()
