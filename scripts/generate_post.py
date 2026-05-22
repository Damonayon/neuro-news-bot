"""
generate_post.py — ФИНАЛЬНАЯ ВЕРСИЯ
Автоматически переключается между 3 бесплатными моделями если одна занята.
"""

import os, json, time, random, hashlib, urllib.parse
import requests, feedparser
from datetime import datetime, timezone

BOT_TOKEN      = os.environ["TELEGRAM_BOT_TOKEN"]
MODERATOR_ID   = os.environ["TELEGRAM_MODERATOR_ID"]
CHANNEL_ID     = os.environ["TELEGRAM_CHANNEL_ID"]
OPENROUTER_KEY = os.environ["OPENROUTER_KEY"]

# 3 бесплатные модели — перебираем по очереди если одна занята
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
                    "summary": entry.get("summary", "")[:600].strip(),
                })
        except Exception as e:
            print(f"Ошибка загрузки {feed_url}: {e}")
    print(f"Статей из RSS: {len(articles)}")
    return articles


PROMPT_TEMPLATE = """Ты — редактор Telegram-канала «Нейро-новости».

На основе этой новости об ИИ:
Заголовок: {title}
Краткое содержание: {summary}
Ссылка: {url}

Напиши ответ СТРОГО в формате JSON (и ничего кроме JSON):
{{"post": "текст поста", "image_prompt": "english image generation prompt"}}

Требования к полю "post":
- Первые 1-2 строки — мощный хук, который остановит скролл
- Объясни суть простым языком без жаргона
- Живой комментарий: почему это важно для обычного человека
- 3-5 эмодзи уместно по тексту
- Максимум 900 символов
- В конце: 3 хештега (#ИИ #нейросети + тематический)
- Последняя строка: 🔗 {url}
- Стиль: умный друг рассказывает за кофе
- Запрещены слова: «революция», «прорыв», «невероятный»

Требования к полю "image_prompt":
- На английском языке
- Концептуальная картинка к теме новости
- Стиль: futuristic digital art, minimalist, clean, 4k
- Максимум 100 символов

Верни ТОЛЬКО JSON без markdown-блоков и без пояснений."""


def call_ai(prompt):
    """Перебирает модели по очереди — если одна занята, берёт следующую."""
    last_error = None
    for model in FREE_MODELS:
        print(f"Пробую модель: {model}")
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
                    "max_tokens": 1000,
                },
                timeout=45,
            )
            if response.status_code == 200:
                content = response.json()["choices"][0]["message"]["content"]
                print(f"Успешно! Модель: {model}")
                return content.strip()
            elif response.status_code == 429:
                print(f"Модель {model} занята (429), пробую следующую...")
                time.sleep(3)
                last_error = f"429 на {model}"
                continue
            else:
                print(f"Модель {model} вернула {response.status_code}, пробую следующую...")
                last_error = f"{response.status_code} на {model}: {response.text[:200]}"
                continue
        except Exception as e:
            print(f"Ошибка с моделью {model}: {e}")
            last_error = str(e)
            continue

    raise RuntimeError(f"Все модели недоступны. Последняя ошибка: {last_error}")


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
        image_prompt = data.get("image_prompt", "AI technology concept art futuristic").strip()
        if not post_text:
            raise ValueError("Пустой пост")
        return post_text, image_prompt
    except Exception as e:
        print(f"Не удалось распарсить JSON: {e}. Использую сырой текст.")
        return raw.strip(), "AI technology neural network futuristic digital art"


def build_image_url(image_prompt):
    seed    = random.randint(1, 99999)
    encoded = urllib.parse.quote(image_prompt)
    return (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width=1200&height=630&nologo=true&seed={seed}"
    )


def send_for_approval(post_text, image_url, art_id):
    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ Опубликовать", "callback_data": f"approve_{art_id}"},
            {"text": "❌ Отклонить",    "callback_data": f"reject_{art_id}"},
        ]]
    }
    caption = f"📬 Новый пост — нужно одобрение\n\n{post_text}"

    result = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
        json={
            "chat_id":      MODERATOR_ID,
            "photo":        image_url,
            "caption":      caption[:1024],
            "reply_markup": keyboard,
        },
        timeout=15,
    ).json()

    if not result.get("ok"):
        print("Картинка не загрузилась, отправляю текстом...")
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
        raise RuntimeError(f"Не удалось отправить сообщение: {result}")

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
            print("Новых статей нет. Завершаем.")
            return

        article = new_articles[0]
        print(f"Обрабатываем: {article['title'][:80]}")

        post_text, image_prompt = generate_content(article)
        print(f"Пост готов: {len(post_text)} символов")

        image_url = build_image_url(image_prompt)
        msg_id    = send_for_approval(post_text, image_url, article["id"])
        print(f"Отправлено модератору, message_id={msg_id}")

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
