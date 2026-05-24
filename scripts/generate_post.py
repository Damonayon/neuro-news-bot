"""generate_post.py — генератор постов (версия с БД).

Особенности:
- Универсальная архитектура: одна кодовая база для любого канала
- Конфигурация через pydantic-settings (валидация на старте, см. bot/config.py)
- Хранилище: SQLite через SQLAlchemy (см. bot/storage.py)
- Умный фильтр контента (GPT-4o оценивает HIGH/MEDIUM/LOW)
- Эталонные примеры вирусных постов в промпте (few-shot learning)
- Жёсткие правила SMM
- Гарантированные рабочие гиперссылки

Запуск: python scripts/generate_post.py
"""

from __future__ import annotations

import json
import os
import random
import re
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import Tuple

import feedparser
import requests

# Добавляем корень проекта в sys.path, чтобы `from bot...` работало
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bot.config import get_settings  # noqa: E402
from bot.db import init_db, session_scope  # noqa: E402
from bot.storage import (  # noqa: E402
    article_hash,
    create_pending_post,
    ensure_channel,
    known_article_hashes,
    save_article,
)


# ─── Константы внешних сервисов ──────────────────────────────────────────────
GITHUB_MODELS_URL = "https://models.inference.ai.azure.com/chat/completions"
MODELS = ["gpt-4o", "gpt-4o-mini"]

# Сколько последних статей брать из каждого RSS-фида
ENTRIES_PER_FEED = 5
# Сколько кандидатов прогонять через фильтр качества за один запуск
MAX_CANDIDATES_TO_FILTER = 10


# ─── Конфигурация ────────────────────────────────────────────────────────────
settings = get_settings()


# ─── Утилиты HTTP ────────────────────────────────────────────────────────────


def notify_moderator(text: str) -> None:
    """Отправка алерта модератору. Сетевые ошибки не пробрасываем."""
    try:
        requests.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={"chat_id": settings.telegram_moderator_id, "text": text},
            timeout=10,
        )
    except requests.RequestException as exc:
        print(f"  notify_moderator failed: {exc}")


# ─── Загрузка статей из RSS ──────────────────────────────────────────────────


def fetch_articles() -> list[dict]:
    articles: list[dict] = []
    for feed_url in settings.rss_feeds:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:ENTRIES_PER_FEED]:
                url = entry.get("link", "")
                if not url:
                    continue
                summary = re.sub(r"<[^>]+>", "", entry.get("summary", ""))
                articles.append(
                    {
                        "id": article_hash(url),
                        "title": entry.get("title", "").strip(),
                        "url": url,
                        "summary": summary[:800].strip(),
                        "source_feed": feed_url,
                    }
                )
        except Exception as exc:
            print(f"Ошибка RSS {feed_url}: {exc}")
    print(f"Всего статей из RSS: {len(articles)}")
    return articles


# ─── Промпты ─────────────────────────────────────────────────────────────────


FILTER_SYSTEM = f"""Ты — главный редактор Telegram-канала «{settings.channel_topic}» про {settings.channel_niche}.
Аудитория: {settings.channel_audience}.
Твоя задача — оценивать пригодность статей для публикации."""

FILTER_PROMPT = """Оцени, подходит ли эта статья для публикации в канале.

ЗАГОЛОВОК: {title}
СОДЕРЖАНИЕ: {summary}

Подходящие статьи (HIGH):
✅ Новости о релизах, запусках, продуктах
✅ Скандалы, увольнения, корпоративные события
✅ Прорывы, рекорды, конкретные достижения с цифрами
✅ Новые инструменты, которые читатель может попробовать сегодня
✅ Кейсы применения с конкретным результатом

Подходящие, но не идеально (MEDIUM):
⚠️ Аналитика рынка, тренды, прогнозы
⚠️ Интервью с известными людьми
⚠️ Сравнения продуктов

НЕ подходят (LOW):
❌ Учебники, туториалы, "как сделать X"
❌ Философские рассуждения о будущем
❌ Чисто академические/научные статьи
❌ Личные блоги ("как я сделал Y")
❌ Реклама услуг и продуктов

Верни СТРОГО JSON:
{{"quality": "HIGH" | "MEDIUM" | "LOW", "reason": "краткое обоснование на русском"}}"""


GENERATOR_SYSTEM = f"""Ты — главный редактор топового Telegram-канала «{settings.channel_topic}».
Тема канала: {settings.channel_niche}.
Аудитория: {settings.channel_audience}.
Язык: {settings.channel_lang}.

Ты пишешь как лучшие SMM-специалисты России: цепляюще, конкретно, с цифрами и пользой.
Каждый пост должен заставить читателя остановиться, прочитать до конца и поделиться.

Отвечай ТОЛЬКО валидным JSON."""


GENERATOR_PROMPT = """Напиши идеальный Telegram-пост на основе этой новости.

НОВОСТЬ:
Заголовок: {title}
Содержание: {summary}
Ссылка: {url}

Тип контента: {rubric}

═══════════════════════════════════════════════
ЭТАЛОННЫЕ ПРИМЕРЫ ВИРУСНЫХ ПОСТОВ (учись на них!)
═══════════════════════════════════════════════

ПРИМЕР 1 (новый инструмент):
─────────────────────────────
🚨 Новый инструмент собрал <b>50 000 пользователей</b> за 48 часов. И он бесплатный.

Vercel запустил v0 — нейросеть, которая по описанию рисует готовый интерфейс сайта. Пишешь «дашборд для продаж с графиками» — получаешь рабочий React-компонент за 30 секунд.

Если ты <b>предприниматель</b> — это значит, что MVP теперь делается за вечер, а не за неделю.
Если ты <b>дизайнер</b> — пора учиться промптингу, иначе твою работу заберут.
Если ты <b>разработчик</b> — это твой новый Stack Overflow на стероидах.

Готовы ли мы к миру, где код пишет ИИ, а человек только редактирует? 🤔

#ИИ #нейросети #инструменты

<a href="URL">📖 Читать полностью</a>

─────────────────────────────

ПРИМЕР 2 (срочная новость / скандал):
─────────────────────────────
🔻 OpenAI <b>уволил 300 контент-модераторов</b>. Их работу теперь делает GPT-4.

Компания первой в индустрии полностью заменила людей-модераторов на свою же модель. По данным Bloomberg, это сэкономит OpenAI $12 млн в год.

Если ты работаешь в найме на рутинных задачах — посмотри на это <b>дважды</b>. Это не будущее. Это уже настоящее.

А ты бы доверил ИИ модерировать твой контент? 💭

#ИИ #новости #будущее

<a href="URL">📖 Читать полностью</a>

─────────────────────────────

ПРИМЕР 3 (цифра дня / исследование):
─────────────────────────────
📊 <b>73%</b> сотрудников втайне используют ChatGPT на работе. А босс не в курсе.

Стэнфорд опросил 4500 офисных работников. Выводы шокируют:
• 73% используют ИИ ежедневно
• 84% делают это без ведома руководства
• 91% увеличили продуктивность минимум на четверть

«Теневая революция» уже происходит. Только в одной отдельно взятой переговорке.

Ты в этих 73%? 🤫

#ИИ #работа #исследование

<a href="URL">📖 Читать полностью</a>

═══════════════════════════════════════════════
ТРЕБОВАНИЯ К ТВОЕМУ ПОСТУ
═══════════════════════════════════════════════

Верни ТОЛЬКО JSON:
{{"post": "текст поста в HTML", "image_prompt": "english visual prompt"}}

ОБЯЗАТЕЛЬНАЯ СТРУКТУРА:

[Строка 1] КРЮЧОК
Обязательно: либо цифра, либо неожиданный факт, либо провокация.
Эмодзи в начале: 🚨 🔥 🔻 📊 🎯 ⚡ 🤖 🧠
<b>Жирным</b> — ключевую цифру или слово.

[пустая строка]

[Строки 2-4] СУТЬ
3-4 предложения. Конкретика: кто сделал, что, когда, какие цифры.
Простой язык, как другу. Никаких "в данной статье говорится".

[пустая строка]

[Строки 5-7] ПОЛЬЗА ДЛЯ ЧИТАТЕЛЯ
Конкретные сегменты: "Если ты <b>фрилансер</b> — ...", "Для <b>малого бизнеса</b> — ..."
Минимум 2 сегмента. Каждый — с реальной пользой/угрозой.

[пустая строка]

[Строка 8] ВОПРОС/ТЕЗИС
Острый вопрос для комментариев. Эмодзи в конце: 🤔 💭 👀 🔥

[пустая строка]

[Строка 9] #ИИ #нейросети #тематический

[пустая строка]

[Строка 10] <a href="{url}">📖 Читать полностью</a>
⚠️ ВАЖНО: вставь именно эту ссылку дословно с правильным URL!

═══════════════════════════════════════════════
ЖЁСТКИЕ ПРАВИЛА
═══════════════════════════════════════════════

✅ Цифры: минимум одна цифра в посте (лучше в крючке)
✅ <b>Жирный</b>: 3-5 раз для ключевых слов
✅ Эмодзи: 5-8 штук, уместно, не подряд
✅ Длина: 180-260 слов СТРОГО
✅ Язык: только {lang}
✅ Сегменты пользы: минимум 2

❌ Запрещено: «революция», «прорыв», «невероятный», «уникальный», «потрясающий»
❌ Запрещено: вода типа «в данной статье», «как мы знаем», «в современном мире»
❌ Запрещено: общие фразы без цифр и конкретики

═══════════════════════════════════════════════
IMAGE PROMPT (английский, до 130 символов)
═══════════════════════════════════════════════

Стиль: cinematic concept art, dramatic lighting, ultra detailed, 8k
Запрещено: humans, faces, people, text, letters, words

Пример: "glowing AI processor dark space electric blue neon circuits cinematic 8k ultra detailed"

Верни ТОЛЬКО JSON."""


# ─── Вызовы GitHub Models ────────────────────────────────────────────────────


def _call_model(model: str, messages: list[dict], temperature: float, max_tokens: int):
    return requests.post(
        GITHUB_MODELS_URL,
        headers={
            "Authorization": f"Bearer {settings.gh_models_token}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        timeout=60,
    )


def call_ai(
    messages: list[dict], *, temperature: float = 0.7, max_tokens: int = 1500
) -> Tuple[str, str]:
    """Возвращает (content, model_used)."""
    for model in MODELS:
        for attempt in range(3):
            try:
                resp = _call_model(model, messages, temperature, max_tokens)
                if resp.status_code == 200:
                    content = resp.json()["choices"][0]["message"]["content"].strip()
                    return content, model
                if resp.status_code == 429:
                    wait = 20 * (attempt + 1)
                    print(f"  Rate limit {model}, жду {wait}с...")
                    time.sleep(wait)
                    continue
                if resp.status_code in (400, 404):
                    print(f"  {model} недоступна → пробую следующую")
                    break
                print(f"  {model} ошибка {resp.status_code}: {resp.text[:150]}")
                time.sleep(5)
            except requests.RequestException as exc:
                print(f"  Сетевое исключение: {exc}")
                time.sleep(5)
    raise RuntimeError("Все модели недоступны")


def _extract_json(raw: str) -> dict:
    """Достаёт JSON из ответа модели — даже если он в markdown-блоке."""
    cleaned = raw.strip()
    if "```" in cleaned:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
        if match:
            cleaned = match.group(1)
    return json.loads(cleaned)


# ─── Фильтр качества ─────────────────────────────────────────────────────────


def filter_article(article: dict) -> Tuple[str, str]:
    messages = [
        {"role": "system", "content": FILTER_SYSTEM},
        {"role": "user", "content": FILTER_PROMPT.format(**article)},
    ]
    try:
        raw, _ = call_ai(messages, temperature=0.3, max_tokens=200)
        data = _extract_json(raw)
        return data.get("quality", "LOW"), data.get("reason", "")
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        print(f"  Ошибка фильтра: {exc} — считаем MEDIUM")
        return "MEDIUM", "ошибка парсинга"


# ─── Определение рубрики (эвристика) ─────────────────────────────────────────


def detect_rubric(article: dict) -> str:
    text = (article["title"] + " " + article["summary"]).lower()
    if any(w in text for w in ["launch", "release", "запуск", "релиз", "выпустил", "представил", "анонс"]):
        return "🚀 Запуск/Релиз нового продукта"
    if any(w in text for w in ["уволил", "fired", "laid off", "сократил", "закрыл"]):
        return "🔻 Корпоративная новость/Скандал"
    if any(w in text for w in ["%", "percent", "study", "research", "исследование", "опрос"]):
        return "📊 Исследование/Цифра дня"
    if any(w in text for w in ["tool", "app", "инструмент", "приложение", "сервис"]):
        return "🔧 Новый инструмент"
    if any(w in text for w in ["billion", "million", "raised", "funding", "млрд", "млн", "инвест"]):
        return "💰 Инвестиции/Финансы"
    return "🤖 Новость дня"


# ─── Генерация поста ─────────────────────────────────────────────────────────


def parse_post(raw: str) -> Tuple[str, str]:
    data = _extract_json(raw)
    post_text = data.get("post", "").strip()
    image_prompt = data.get("image_prompt", "").strip()

    if not post_text:
        raise ValueError("Пустой пост")

    if settings.channel_lang.lower() == "русский":
        ru = sum(1 for c in post_text if "Ѐ" <= c <= "ӿ")
        if ru < 30:
            raise ValueError(f"Пост не на русском (ru символов: {ru})")

    if not image_prompt:
        image_prompt = "AI neural network dark space neon glow cinematic 8k"

    return post_text, image_prompt


def ensure_correct_link(post_text: str, article_url: str) -> str:
    """Гарантирует наличие правильной гиперссылки в посте."""
    correct = f'<a href="{article_url}">📖 Читать полностью</a>'
    if correct in post_text:
        return post_text
    post_text = re.sub(r"<a\s+href=[^>]*>.*?</a>", "", post_text, flags=re.IGNORECASE | re.DOTALL)
    post_text = re.sub(r"📖\s*Читать\s*полностью", "", post_text, flags=re.IGNORECASE)
    return post_text.rstrip() + f"\n\n{correct}"


def generate_post_content(article: dict, rubric: str) -> Tuple[str, str, str]:
    """Возвращает (post_text, image_prompt, model_used)."""
    prompt = GENERATOR_PROMPT.format(
        title=article["title"],
        summary=article["summary"],
        url=article["url"],
        rubric=rubric,
        lang=settings.channel_lang,
    )
    messages = [
        {"role": "system", "content": GENERATOR_SYSTEM},
        {"role": "user", "content": prompt},
    ]

    last_err: Exception | None = None
    for attempt in range(3):
        try:
            raw, model_used = call_ai(messages, temperature=0.85, max_tokens=1500)
            post_text, image_prompt = parse_post(raw)
            post_text = ensure_correct_link(post_text, article["url"])
            return post_text, image_prompt, model_used
        except (json.JSONDecodeError, ValueError) as exc:
            print(f"  Попытка {attempt + 1}: ошибка парсинга — {exc}")
            last_err = exc
            time.sleep(3)
    raise RuntimeError(f"Не удалось сгенерировать корректный пост: {last_err}")


# ─── Картинки ────────────────────────────────────────────────────────────────


def build_image_url(prompt: str) -> str:
    seed = random.randint(10000, 99999)
    full = f"{prompt}, NO humans, NO faces, NO text, NO letters, abstract only, professional"
    encoded = urllib.parse.quote(full)
    return (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width=1080&height=1080&model=flux&nologo=true&enhance=true&seed={seed}"
    )


# ─── Telegram: отправка на одобрение ─────────────────────────────────────────


def send_for_approval(post_text: str, image_url: str, art_hash_str: str) -> int:
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ Опубликовать", "callback_data": f"approve_{art_hash_str}"},
                {"text": "❌ Отклонить", "callback_data": f"reject_{art_hash_str}"},
            ]
        ]
    }

    preview = re.sub(r"<[^>]+>", "", post_text)
    caption = f"📬 Новый пост [{settings.channel_topic}]:\n\n{preview}"

    result = requests.post(
        f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendPhoto",
        json={
            "chat_id": settings.telegram_moderator_id,
            "photo": image_url,
            "caption": caption[:1024],
            "reply_markup": keyboard,
        },
        timeout=25,
    ).json()

    if not result.get("ok"):
        print(f"Фото не загрузилось ({result.get('description')}), отправляю текстом")
        result = requests.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
            json={
                "chat_id": settings.telegram_moderator_id,
                "text": caption[:4096],
                "reply_markup": keyboard,
            },
            timeout=10,
        ).json()

    if not result.get("ok"):
        raise RuntimeError(f"Telegram ошибка: {result}")

    return result["result"]["message_id"]


# ─── ГЛАВНАЯ ФУНКЦИЯ ─────────────────────────────────────────────────────────


def main() -> None:
    print(f"\n{'=' * 60}")
    print(f"Канал: «{settings.channel_topic}»  |  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'=' * 60}")

    init_db()

    try:
        articles = fetch_articles()

        # Выясняем что уже видели — одна транзакция чисто на чтение
        with session_scope() as session:
            channel = ensure_channel(session)
            channel_id = channel.id
            known = known_article_hashes(session, channel_id)

        new_articles = [a for a in articles if a["id"] not in known]
        print(f"Новых статей в RSS: {len(new_articles)}")

        if not new_articles:
            print("Нет новых статей.")
            return

        # Фильтрация качества (одна короткая транзакция на каждое кандидат)
        print(f"\nФильтрация качества (top-{min(MAX_CANDIDATES_TO_FILTER, len(new_articles))} кандидатов):")
        best_article: dict | None = None
        first_medium: dict | None = None

        for i, article in enumerate(new_articles[:MAX_CANDIDATES_TO_FILTER]):
            print(f"\n[{i + 1}] {article['title'][:70]}")
            quality, reason = filter_article(article)
            print(f"  → {quality}: {reason}")

            # Сохраняем статью в БД (даже LOW — чтобы не оценивать повторно)
            with session_scope() as session:
                save_article(
                    session,
                    channel_id=channel_id,
                    url=article["url"],
                    title=article["title"],
                    summary=article["summary"],
                    source_feed=article.get("source_feed"),
                    quality=quality,
                    quality_reason=reason,
                )

            if quality == "HIGH":
                best_article = article
                print("  ✅ ВЫБРАНА КАК HIGH")
                break
            if quality == "MEDIUM" and first_medium is None:
                first_medium = article

        if best_article is None:
            best_article = first_medium

        if best_article is None:
            print("\n⚠️ Не нашли подходящих статей в этом цикле.")
            return

        print(f"\n{'─' * 60}")
        print(f"📝 Генерируем пост для: {best_article['title']}")
        print(f"{'─' * 60}")

        rubric = detect_rubric(best_article)
        print(f"  Рубрика: {rubric}")

        post_text, image_prompt, model_used = generate_post_content(best_article, rubric)
        print(f"  Пост готов: {len(post_text)} символов (модель: {model_used})")
        print(f"  Image: {image_prompt[:60]}")

        image_url = build_image_url(image_prompt)
        msg_id = send_for_approval(post_text, image_url, best_article["id"])
        print(f"\n✅ Отправлено модератору (msg_id={msg_id})")

        # Финальная транзакция: сохраняем рубрику и создаём pending-Post
        with session_scope() as session:
            article = save_article(
                session,
                channel_id=channel_id,
                url=best_article["url"],
                title=best_article["title"],
                summary=best_article["summary"],
                source_feed=best_article.get("source_feed"),
                rubric=rubric,
            )
            create_pending_post(
                session,
                article=article,
                channel_id=channel_id,
                post_text=post_text,
                image_url=image_url,
                image_prompt=image_prompt,
                moderator_msg_id=msg_id,
                model_used=model_used,
            )

        print("✅ ГОТОВО\n")

    except Exception as exc:
        msg = f"❌ Ошибка [{settings.channel_topic}]:\n{type(exc).__name__}: {exc}"
        print(msg)
        notify_moderator(msg)
        raise


if __name__ == "__main__":
    main()
