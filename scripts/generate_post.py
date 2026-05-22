"""
generate_post.py — ПРОФЕССИОНАЛЬНАЯ ВЕРСИЯ
"""

import os, json, time, random, hashlib, urllib.parse
import requests, feedparser
from datetime import datetime, timezone

BOT_TOKEN      = os.environ["TELEGRAM_BOT_TOKEN"]
MODERATOR_ID   = os.environ["TELEGRAM_MODERATOR_ID"]
CHANNEL_ID     = os.environ["TELEGRAM_CHANNEL_ID"]
OPENROUTER_KEY = os.environ["OPENROUTER_KEY"]

FREE_MODELS = ["openrouter/free", "openrouter/free", "openrouter/free"]

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


PROMPT_TEMPLATE = """Ты — топ-редактор Telegram-канала «Нейро-новости» с 500 000 подписчиков. Канал про ИИ для русскоязычной аудитории. Твои посты — эталон SMM: они вирусные, цепляющие, умные и человечные одновременно.

НОВОСТЬ:
Заголовок: {title}
Содержание: {summary}
Ссылка: {url}

ЗАДАЧА: напиши идеальный Telegram-пост и промпт для иллюстрации.

Верни СТРОГО JSON без markdown и пояснений:
{{"post": "текст поста в HTML", "image_prompt": "промпт на английском"}}

═══ ПРАВИЛА ДЛЯ ПОЛЯ "post" ═══

СТРУКТУРА ПОСТА (строго соблюдать):

1. СТРОКА-КРЮЧОК (1 строка)
   — Интригующий факт, неожиданный угол, провокационный вопрос ИЛИ шокирующая цифра
   — Читатель должен ОСТАНОВИТЬ скролл
   — Примеры хороших крючков:
     "ИИ только что уволил 300 юристов. И это только начало 🔻"
     "ChatGPT врёт в 23% случаев. Вот как это проверить 👇"
     "Эта нейросеть делает за 3 секунды то, на что у дизайнера уходит день 🤯"

2. ПУСТАЯ СТРОКА

3. СУТЬ (2-3 предложения)
   — Что произошло — коротко и ясно
   — Никакого жаргона, как другу за кофе
   — Конкретные цифры и факты если есть

4. ПУСТАЯ СТРОКА

5. ПОЧЕМУ ЭТО ВАЖНО ЛИЧНО ДЛЯ ТЕБЯ (2-3 предложения)
   — Как это изменит жизнь/работу обычного человека
   — Конкретно: «Если ты дизайнер — это значит...», «Для малого бизнеса — это...»
   — Эмоциональный, живой язык

6. ПУСТАЯ СТРОКА

7. ОСТРЫЙ ВОПРОС или МНЕНИЕ редакции (1 строка)
   — Что-то, что заставит написать комментарий
   — Дискуссионный тезис или интересный вопрос аудитории

8. ПУСТАЯ СТРОКА

9. ХЕШТЕГИ: ровно 3, через пробел: #ИИ и два тематических

10. ПУСТАЯ СТРОКА

11. Последняя строка: <a href="{url}">📖 Читать полностью</a>

ФОРМАТИРОВАНИЕ (Telegram HTML):
— <b>жирный</b> — для ключевых слов (2-4 раза в посте, не больше)
— <i>курсив</i> — для цитат или акцентов
— Эмодзи: 4-6 штук, уместно, не в каждой строке
— Длина: 180-280 слов
— Запрещены слова: «революция», «прорыв», «невероятный», «потрясающий», «уникальный»
— Тон: умный, живой, чуть дерзкий — как Medium + немного Дудь

═══ ПРАВИЛА ДЛЯ ПОЛЯ "image_prompt" ═══
— На английском
— Концептуальная, атмосферная иллюстрация к теме
— Стиль: cinematic digital art, dramatic lighting, ultra detailed, 8k, professional
— Пример: "glowing AI brain neural network dark background neon blue purple cinematic 8k ultra detailed"
— НЕ упоминать людей, лица, текст на картинке
— Максимум 120 символов

Верни ТОЛЬКО JSON."""


def call_ai(prompt):
    last_error = None
    for model in FREE_MODELS:
        try:
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_KEY}",
                    "Content-Type":  "application/json",
                },
                json={
                    "model":      model,
                    "messages":   [{"role": "user", "content": prompt}],
                    "max_tokens": 1500,
                    "temperature": 0.85,
                },
                timeout=60,
            )
            if response.status_code == 200:
                content = response.json()["choices"][0]["message"]["content"]
                print(f"Модель ответила успешно")
                return content.strip()
            elif response.status_code == 429:
                print(f"Модель занята (429), пробую снова через 15с...")
                time.sleep(15)
                last_error = "429 rate limit"
                continue
            else:
                last_error = f"{response.status_code}: {response.text[:200]}"
                time.sleep(5)
                continue
        except Exception as e:
            last_error = str(e)
            time.sleep(5)
            continue
    raise RuntimeError(f"Все попытки исчерпаны. Последняя ошибка: {last_error}")


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
        print(f"Не удалось распарсить JSON: {e}")
        return raw.strip(), "AI technology neural network cinematic dark background 8k"


def build_image_url(image_prompt):
    """Pollinations AI — максимальное качество"""
    seed    = random.randint(10000, 99999)
    encoded = urllib.parse.quote(image_prompt + ", no text, no watermark, professional")
    return (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width=1280&height=720&model=flux&nologo=true&enhance=true&seed={seed}"
    )


def send_for_approval(post_text, image_url, art_id):
    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ Опубликовать", "callback_data": f"approve_{art_id}"},
            {"text": "❌ Отклонить",    "callback_data": f"reject_{art_id}"},
        ]]
    }

    # Превью для модератора (без HTML-тегов для caption)
    import re
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
        timeout=20,
    ).json()

    if not result.get("ok"):
        print(f"Фото не загрузилось: {result.get('description')}, отправляю текстом...")
        result = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id":      MODERATOR_ID,
                "text":         f"📬 Новый пост на одобрение:\n\n{clean_preview[:3800]}",
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
        print(f"Image prompt: {image_prompt}")

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
        error_msg = f"❌ Ошибка генерации поста:\n{type(e).__name__}: {e}"
        print(error_msg)
        notify_moderator(error_msg)
        raise


if __name__ == "__main__":
    main()
