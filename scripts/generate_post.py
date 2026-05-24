"""
generate_post.py — ПРОФЕССИОНАЛЬНАЯ ВЕРСИЯ для сети каналов

Особенности:
- Универсальная архитектура: одна кодовая база для любого канала
- Конфигурация канала через переменные окружения (тематика, источники, стиль)
- Умный фильтр контента: отбирает только новости-релизы, отсеивает учебники/философию
- Динамические рубрики: бот сам распознаёт тип новости и выбирает формат
- Эталонные примеры вирусных постов в промпте (few-shot learning)
- Жёсткие правила SMM: цифры в крючке, конкретная польза, разбивка структуры
- Гарантированные рабочие гиперссылки (Telegram HTML)
"""

import os, json, time, random, hashlib, urllib.parse, re
import requests, feedparser
from datetime import datetime, timezone

# ─── Telegram + GitHub Models ────────────────────────────────────────────────
BOT_TOKEN    = os.environ["TELEGRAM_BOT_TOKEN"]
MODERATOR_ID = os.environ["TELEGRAM_MODERATOR_ID"]
CHANNEL_ID   = os.environ["TELEGRAM_CHANNEL_ID"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]

GITHUB_MODELS_URL = "https://models.inference.ai.azure.com/chat/completions"
MODELS = ["gpt-4o", "gpt-4o-mini"]

# ─── Конфигурация канала (через переменные окружения) ────────────────────────
# Это позволяет одним и тем же кодом обслуживать ЛЮБОЙ канал сети.
# Для нового канала просто создаёшь новый репозиторий и меняешь эти переменные.

CHANNEL_TOPIC = os.environ.get("CHANNEL_TOPIC", "Нейро-новости")
CHANNEL_NICHE = os.environ.get("CHANNEL_NICHE", "искусственный интеллект и нейросети")
CHANNEL_AUDIENCE = os.environ.get(
    "CHANNEL_AUDIENCE",
    "русскоязычные, 18-45 лет, интересуются технологиями и будущим"
)
CHANNEL_LANG = os.environ.get("CHANNEL_LANG", "русский")

# RSS-источники конкретно для этой ниши (можно переопределить через secrets)
DEFAULT_FEEDS = {
    "ai": [
        "https://habr.com/ru/rss/hub/artificial_intelligence/all/",
        "https://habr.com/ru/rss/hub/machine_learning/all/",
        "https://techcrunch.com/category/artificial-intelligence/feed/",
        "https://venturebeat.com/category/ai/feed/",
        "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
        "https://openai.com/blog/rss/",
        "https://blogs.nvidia.com/feed/",
    ],
}
RSS_FEEDS_RAW = os.environ.get("RSS_FEEDS", "")
RSS_FEEDS = [u.strip() for u in RSS_FEEDS_RAW.split(",") if u.strip()] or DEFAULT_FEEDS["ai"]

DATA_DIR     = "data"
PENDING_FILE = f"{DATA_DIR}/pending.json"
POSTED_FILE  = f"{DATA_DIR}/posted_ids.json"


# ─── Утилиты ──────────────────────────────────────────────────────────────────

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


# ─── Загрузка статей из RSS ───────────────────────────────────────────────────

def fetch_articles():
    articles = []
    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:5]:
                url = entry.get("link", "")
                if not url:
                    continue
                summary = re.sub(r'<[^>]+>', '', entry.get("summary", ""))
                articles.append({
                    "id":      article_id(url),
                    "title":   entry.get("title", "").strip(),
                    "url":     url,
                    "summary": summary[:800].strip(),
                })
        except Exception as e:
            print(f"Ошибка {feed_url}: {e}")
    print(f"Всего статей из RSS: {len(articles)}")
    return articles


# ─── ШАГ 1: Фильтр качества статей ────────────────────────────────────────────
# GPT-4o сам оценивает: это настоящая новость для канала или мусор?

FILTER_SYSTEM = f"""Ты — главный редактор Telegram-канала «{CHANNEL_TOPIC}» про {CHANNEL_NICHE}.
Аудитория: {CHANNEL_AUDIENCE}.
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


def call_model(model, messages, temperature=0.7, max_tokens=1500):
    resp = requests.post(
        GITHUB_MODELS_URL,
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Content-Type":  "application/json",
        },
        json={
            "model":       model,
            "messages":    messages,
            "max_tokens":  max_tokens,
            "temperature": temperature,
        },
        timeout=60,
    )
    return resp.status_code, resp


def call_ai(messages, temperature=0.7, max_tokens=1500):
    """Вызов с автопереключением на резервную модель."""
    for model in MODELS:
        for attempt in range(3):
            try:
                status, resp = call_model(model, messages, temperature, max_tokens)
                if status == 200:
                    return resp.json()["choices"][0]["message"]["content"].strip()
                elif status == 429:
                    wait = 20 * (attempt + 1)
                    print(f"  Rate limit {model}, жду {wait}с...")
                    time.sleep(wait)
                elif status in (404, 400):
                    print(f"  {model} недоступна → пробую следующую")
                    break
                else:
                    print(f"  {model} ошибка {status}: {resp.text[:150]}")
                    time.sleep(5)
            except Exception as e:
                print(f"  Исключение: {e}")
                time.sleep(5)
    raise RuntimeError("Все модели недоступны")


def filter_article(article):
    """Возвращает уровень качества статьи: HIGH/MEDIUM/LOW"""
    messages = [
        {"role": "system", "content": FILTER_SYSTEM},
        {"role": "user",   "content": FILTER_PROMPT.format(**article)},
    ]
    try:
        raw = call_ai(messages, temperature=0.3, max_tokens=200)
        clean = raw.strip()
        if "```" in clean:
            for p in clean.split("```"):
                p = p.strip()
                if p.startswith("json"): p = p[4:].strip()
                if p.startswith("{"): clean = p; break
        data = json.loads(clean)
        return data.get("quality", "LOW"), data.get("reason", "")
    except Exception as e:
        print(f"  Ошибка фильтра: {e} — считаем MEDIUM")
        return "MEDIUM", "ошибка парсинга"


# ─── ШАГ 2: Генерация поста с эталонными примерами ───────────────────────────

GENERATOR_SYSTEM = f"""Ты — главный редактор топового Telegram-канала «{CHANNEL_TOPIC}».
Тема канала: {CHANNEL_NICHE}.
Аудитория: {CHANNEL_AUDIENCE}.
Язык: {CHANNEL_LANG}.

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


def detect_rubric(article):
    """Простая эвристика — определяет тип новости по ключевым словам."""
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


def parse_response(raw):
    clean = raw.strip()
    if "```" in clean:
        for p in clean.split("```"):
            p = p.strip()
            if p.startswith("json"): p = p[4:].strip()
            if p.startswith("{"): clean = p; break

    data         = json.loads(clean)
    post_text    = data.get("post", "").strip()
    image_prompt = data.get("image_prompt", "").strip()

    if not post_text:
        raise ValueError("Пустой пост")

    # Проверка языка (для русского канала)
    if CHANNEL_LANG.lower() == "русский":
        ru = sum(1 for c in post_text if '\u0400' <= c <= '\u04FF')
        if ru < 30:
            raise ValueError(f"Пост не на русском (ru символов: {ru})")

    if not image_prompt:
        image_prompt = "AI neural network dark space neon glow cinematic 8k"

    return post_text, image_prompt


def ensure_correct_link(post_text, article_url):
    """
    Гарантия что в посте правильная гиперссылка.
    Если модель пропустила или испортила — добавляем её принудительно.
    """
    # Проверяем есть ли правильная гиперссылка
    correct_link = f'<a href="{article_url}">📖 Читать полностью</a>'

    # Если ссылка уже корректная — оставляем
    if correct_link in post_text:
        return post_text

    # Удаляем все попытки сделать гиперссылку (могли быть с ошибками)
    post_text = re.sub(r'<a\s+href=[^>]*>.*?</a>', '', post_text, flags=re.IGNORECASE | re.DOTALL)
    post_text = re.sub(r'📖\s*Читать\s*полностью', '', post_text, flags=re.IGNORECASE)
    post_text = post_text.rstrip()

    # Добавляем правильную ссылку в конец
    return post_text + f"\n\n{correct_link}"


def generate_content(article):
    rubric = detect_rubric(article)
    print(f"  Рубрика: {rubric}")

    prompt = GENERATOR_PROMPT.format(
        title=article["title"],
        summary=article["summary"],
        url=article["url"],
        rubric=rubric,
        lang=CHANNEL_LANG,
    )
    messages = [
        {"role": "system", "content": GENERATOR_SYSTEM},
        {"role": "user",   "content": prompt},
    ]

    for attempt in range(3):
        try:
            raw = call_ai(messages, temperature=0.85, max_tokens=1500)
            post_text, image_prompt = parse_response(raw)
            post_text = ensure_correct_link(post_text, article["url"])
            return post_text, image_prompt
        except (json.JSONDecodeError, ValueError) as e:
            print(f"  Попытка {attempt+1}: ошибка парсинга — {e}")
            time.sleep(3)

    raise RuntimeError("Не удалось сгенерировать корректный пост")


def build_image_url(prompt):
    seed    = random.randint(10000, 99999)
    full    = f"{prompt}, NO humans, NO faces, NO text, NO letters, abstract only, professional"
    encoded = urllib.parse.quote(full)
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

    preview = re.sub(r'<[^>]+>', '', post_text)
    caption = f"📬 Новый пост [{CHANNEL_TOPIC}]:\n\n{preview}"

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
        print(f"Фото не загрузилось ({result.get('description')}), отправляю текстом")
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
        raise RuntimeError(f"Telegram ошибка: {result}")

    return result["result"]["message_id"]


# ─── ГЛАВНАЯ ФУНКЦИЯ ──────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*60}")
    print(f"Канал: «{CHANNEL_TOPIC}»  |  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")

    try:
        posted_ids   = load_json(POSTED_FILE, [])
        pending      = load_json(PENDING_FILE, {})
        articles     = fetch_articles()
        new_articles = [a for a in articles if a["id"] not in posted_ids]
        print(f"Новых статей в RSS: {len(new_articles)}")

        if not new_articles:
            print("Нет новых статей.")
            return

        # ФИЛЬТРАЦИЯ: ищем лучшую статью
        print(f"\nФильтрация качества (top-{min(10, len(new_articles))} кандидатов):")
        best_article = None
        first_medium = None

        for i, article in enumerate(new_articles[:10]):
            print(f"\n[{i+1}] {article['title'][:70]}")
            quality, reason = filter_article(article)
            print(f"  → {quality}: {reason}")

            # Помечаем как обработанную в любом случае
            posted_ids.append(article["id"])

            if quality == "HIGH":
                best_article = article
                print(f"  ✅ ВЫБРАНА КАК HIGH")
                break
            elif quality == "MEDIUM" and first_medium is None:
                first_medium = article

        # Если HIGH не нашли — берём первую MEDIUM
        if not best_article:
            best_article = first_medium

        if not best_article:
            print("\n⚠️ Не нашли подходящих статей в этом цикле. Попробуем в следующий раз.")
            posted_ids = posted_ids[-500:]
            save_json(POSTED_FILE, posted_ids)
            return

        print(f"\n{'─'*60}")
        print(f"📝 Генерируем пост для: {best_article['title']}")
        print(f"{'─'*60}")

        post_text, image_prompt = generate_content(best_article)
        print(f"  Пост готов: {len(post_text)} символов")
        print(f"  Image: {image_prompt[:60]}")

        image_url = build_image_url(image_prompt)
        msg_id    = send_for_approval(post_text, image_url, best_article["id"])
        print(f"\n✅ Отправлено модератору (msg_id={msg_id})")

        pending[best_article["id"]] = {
            "post_text":  post_text,
            "image_url":  image_url,
            "title":      best_article["title"],
            "url":        best_article["url"],
            "msg_id":     msg_id,
            "channel":    CHANNEL_TOPIC,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        posted_ids = posted_ids[-500:]

        save_json(PENDING_FILE, pending)
        save_json(POSTED_FILE,  posted_ids)
        print("✅ ГОТОВО\n")

    except Exception as e:
        msg = f"❌ Ошибка [{CHANNEL_TOPIC}]:\n{type(e).__name__}: {e}"
        print(msg)
        notify_moderator(msg)
        raise

if __name__ == "__main__":
    main()
