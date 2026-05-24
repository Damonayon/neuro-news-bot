"""bot.config — единая точка чтения и валидации конфигурации.

Источник конфигурации — переменные окружения (env). Это работает одинаково
и в GitHub Actions (через `env:` блок workflow), и при локальном запуске
(через .env-файл, который pydantic-settings подгружает автоматически).

При старте скрипт получает понятную ошибку, если что-то не задано,
вместо невнятного KeyError из глубины кода.
"""

from __future__ import annotations

from pathlib import Path
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Базовый каталог проекта (на два уровня выше этого файла: bot/ → корень)
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = PROJECT_ROOT / "data"

# RSS-источники по умолчанию для ниши «ИИ» — используются,
# если переменная окружения RSS_FEEDS не задана.
DEFAULT_AI_FEEDS: List[str] = [
    "https://habr.com/ru/rss/hub/artificial_intelligence/all/",
    "https://habr.com/ru/rss/hub/machine_learning/all/",
    "https://techcrunch.com/category/artificial-intelligence/feed/",
    "https://venturebeat.com/category/ai/feed/",
    "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "https://openai.com/blog/rss/",
    "https://blogs.nvidia.com/feed/",
]


class Settings(BaseSettings):
    """Конфигурация бота. Поля без дефолтов = обязательны."""

    # ─── Telegram + AI ────────────────────────────────────────────────
    telegram_bot_token: str = Field(..., alias="TELEGRAM_BOT_TOKEN")
    telegram_moderator_id: str = Field(..., alias="TELEGRAM_MODERATOR_ID")
    telegram_channel_id: str = Field(..., alias="TELEGRAM_CHANNEL_ID")
    gh_models_token: str = Field(..., alias="GH_MODELS_TOKEN")

    # ─── Конфигурация канала ─────────────────────────────────────────
    channel_topic: str = Field("Нейро-новости", alias="CHANNEL_TOPIC")
    channel_niche: str = Field(
        "искусственный интеллект и нейросети", alias="CHANNEL_NICHE"
    )
    channel_audience: str = Field(
        "русскоязычные, 18-45 лет, интересуются технологиями и будущим",
        alias="CHANNEL_AUDIENCE",
    )
    channel_lang: str = Field("русский", alias="CHANNEL_LANG")

    # ─── RSS-источники (строка через запятую) ─────────────────────────
    rss_feeds_raw: str = Field("", alias="RSS_FEEDS")

    # ─── Технические настройки ───────────────────────────────────────
    db_url: str = Field(
        f"sqlite:///{DATA_DIR / 'bot.db'}",
        alias="DB_URL",
        description="URL подключения к БД. По умолчанию — SQLite-файл в data/.",
    )

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @field_validator("telegram_moderator_id", "telegram_channel_id")
    @classmethod
    def _strip_whitespace(cls, v: str) -> str:
        return v.strip()

    @property
    def rss_feeds(self) -> List[str]:
        """Парсим строку RSS_FEEDS в список. Если пусто — дефолтные ИИ-фиды."""
        feeds = [u.strip() for u in self.rss_feeds_raw.split(",") if u.strip()]
        return feeds or DEFAULT_AI_FEEDS

    @property
    def channel_slug(self) -> str:
        """Технический идентификатор канала из его названия."""
        return _slugify(self.channel_topic)


def _slugify(text: str) -> str:
    """Превращает 'Нейро-новости' в 'nejro-novosti'-подобный slug."""
    import re
    from unicodedata import normalize

    # Транслитерация кириллицы (упрощённая, для slug)
    table = str.maketrans(
        "абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
        "abvgdeejziyklmnoprstufhc4w8_y_eua",
    )
    s = text.lower().translate(table)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "channel"


# Удобный синглтон. Создаётся лениво при первом обращении.
_settings: Settings | None = None


def get_settings() -> Settings:
    """Возвращает синглтон конфигурации.

    При первой ошибке валидации показывает понятное сообщение и завершает процесс.
    """
    global _settings
    if _settings is None:
        try:
            _settings = Settings()  # type: ignore[call-arg]
        except Exception as exc:
            import sys

            print("\n❌ Ошибка конфигурации:\n", file=sys.stderr)
            print(exc, file=sys.stderr)
            print(
                "\nПроверь, что заданы переменные окружения:"
                "\n  TELEGRAM_BOT_TOKEN"
                "\n  TELEGRAM_MODERATOR_ID"
                "\n  TELEGRAM_CHANNEL_ID"
                "\n  GH_MODELS_TOKEN"
                "\n(в GitHub: Settings → Secrets and variables → Actions)\n",
                file=sys.stderr,
            )
            sys.exit(1)
    return _settings
