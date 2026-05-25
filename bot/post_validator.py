"""bot.post_validator — sanity-валидация SMM-поста на Python-уровне.

Проверяем все требования из CLAUDE.md прежде, чем тратить ресурсы модератора
на просмотр и AI-критика на оценку. Если проверка падает — пост перегенерируется.

Принципы:
- WARNING-проверки не блокируют (например, ровно 2 эмодзи — отметим, но пропустим)
- ERROR-проверки блокируют: пустой текст, нет ссылки, нерусский, слишком короткий
- Все проверки чистые функции — легко тестируются

Использование:
    from bot.post_validator import validate_post, ValidationResult

    result = validate_post(post_text, article_url=url, language="русский")
    if not result.ok:
        # перегенерировать
        ...
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ─── Параметры (можно вынести в settings, если потребуется per-channel) ──────

MIN_WORDS = 150
MAX_WORDS = 300
MIN_DIGITS = 1
MIN_BOLD = 2  # минимум <b>...</b>
MAX_BOLD = 8
MIN_EMOJI = 3
MAX_EMOJI = 12
MIN_HASHTAGS = 1
MAX_HASHTAGS = 5
MIN_CYRILLIC_RATIO = 0.6  # для русского канала

# Запрещённые слова (см. CLAUDE.md)
BANNED_WORDS = {
    "революция",
    "прорыв",
    "невероятный",
    "невероятная",
    "уникальный",
    "уникальная",
    "потрясающий",
    "потрясающая",
    "в данной статье",
    "как мы знаем",
    "в современном мире",
}


# ─── Результат ───────────────────────────────────────────────────────────────


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, int | float] = field(default_factory=dict)

    def summary(self) -> str:
        """Краткая сводка для логов."""
        if self.ok:
            return f"OK ({self.stats})"
        return f"FAIL: {'; '.join(self.errors)}"


# ─── Утилиты ─────────────────────────────────────────────────────────────────


_EMOJI_RE = re.compile(
    "["
    "\U0001f300-\U0001f9ff"  # symbols & pictographs, transport, misc
    "\U0001f600-\U0001f64f"  # emoticons
    "\U0001f680-\U0001f6ff"  # transport
    "\U00002600-\U000027bf"  # misc symbols, dingbats
    "\U0001fa70-\U0001faff"  # extended symbols
    "]"
)

_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
_HASHTAG_RE = re.compile(r"#[\wЀ-ӿ]+")
_BOLD_RE = re.compile(r"<b>.*?</b>", re.DOTALL)


def _strip_html(text: str) -> str:
    return _TAG_RE.sub("", text)


def _count_words(text: str) -> int:
    clean = _strip_html(text)
    return len([w for w in re.split(r"\s+", clean.strip()) if w])


def _count_cyrillic(text: str) -> int:
    return sum(1 for ch in text if "Ѐ" <= ch <= "ӿ")


def _count_letters(text: str) -> int:
    """Любые буквы (для оценки доли кириллицы)."""
    return sum(1 for ch in text if ch.isalpha())


# ─── Главная функция ─────────────────────────────────────────────────────────


def validate_post(
    post_text: str,
    *,
    article_url: str,
    language: str = "русский",
) -> ValidationResult:
    """Полный sanity-чек поста перед отправкой модератору."""
    errors: list[str] = []
    warnings: list[str] = []
    stats: dict[str, int | float] = {}

    if not post_text or not post_text.strip():
        return ValidationResult(ok=False, errors=["empty post"])

    text = post_text.strip()
    clean = _strip_html(text)
    stats["len_chars"] = len(text)
    stats["len_clean_chars"] = len(clean)

    # ─── длина в словах ──────────────────────────────────────────────
    words = _count_words(text)
    stats["words"] = words
    if words < MIN_WORDS:
        errors.append(f"too short: {words} words (min {MIN_WORDS})")
    elif words > MAX_WORDS:
        errors.append(f"too long: {words} words (max {MAX_WORDS})")

    # ─── цифры ───────────────────────────────────────────────────────
    digits = sum(1 for ch in clean if ch.isdigit())
    stats["digits"] = digits
    if digits < MIN_DIGITS:
        errors.append(f"no digits (min {MIN_DIGITS})")

    # ─── <b> ────────────────────────────────────────────────────────
    bold_count = len(_BOLD_RE.findall(text))
    stats["bold"] = bold_count
    if bold_count < MIN_BOLD:
        errors.append(f"too few <b> tags: {bold_count} (min {MIN_BOLD})")
    elif bold_count > MAX_BOLD:
        warnings.append(f"many <b> tags: {bold_count} (recommended ≤{MAX_BOLD})")

    # ─── эмодзи ──────────────────────────────────────────────────────
    emoji_count = len(_EMOJI_RE.findall(text))
    stats["emoji"] = emoji_count
    if emoji_count < MIN_EMOJI:
        errors.append(f"too few emoji: {emoji_count} (min {MIN_EMOJI})")
    elif emoji_count > MAX_EMOJI:
        warnings.append(f"too many emoji: {emoji_count} (max {MAX_EMOJI})")

    # ─── хэштеги ─────────────────────────────────────────────────────
    hashtags = _HASHTAG_RE.findall(text)
    stats["hashtags"] = len(hashtags)
    if len(hashtags) < MIN_HASHTAGS:
        errors.append(f"no hashtags (min {MIN_HASHTAGS})")
    elif len(hashtags) > MAX_HASHTAGS:
        warnings.append(f"too many hashtags: {len(hashtags)} (max {MAX_HASHTAGS})")

    # ─── ссылка ──────────────────────────────────────────────────────
    expected_link = f'href="{article_url}"'
    if expected_link not in text:
        errors.append("missing or wrong article link")

    # ─── язык (доля кириллицы для русского канала) ──────────────────
    if language.lower() == "русский":
        cyr = _count_cyrillic(text)
        letters = _count_letters(text)
        ratio = cyr / letters if letters > 0 else 0.0
        stats["cyrillic_ratio"] = round(ratio, 2)
        if ratio < MIN_CYRILLIC_RATIO:
            errors.append(
                f"not enough Russian: {ratio:.0%} cyrillic (min {MIN_CYRILLIC_RATIO:.0%})"
            )

    # ─── запрещённые слова ─────────────────────────────────────────
    text_lower = text.lower()
    found_banned = [w for w in BANNED_WORDS if w in text_lower]
    if found_banned:
        errors.append(f"banned words: {', '.join(found_banned)}")

    return ValidationResult(
        ok=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        stats=stats,
    )
