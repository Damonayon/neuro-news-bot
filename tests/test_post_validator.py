"""Тесты bot.post_validator — sanity-валидация поста."""

from __future__ import annotations

from bot.post_validator import (
    MIN_BOLD,
    MIN_DIGITS,
    MIN_EMOJI,
    MIN_WORDS,
    validate_post,
)

URL = "https://example.com/article"
GOOD_LINK = f'<a href="{URL}">📖 Читать полностью</a>'


def _build_post(
    *,
    body_words: int = 200,
    digits: int = 1,
    bold: int = 3,
    emoji: int = 5,
    hashtags: int = 3,
    cyrillic: bool = True,
    banned: str | None = None,
    link: str | None = None,
) -> str:
    """Конструктор валидного поста (или с управляемыми нарушениями)."""
    word = "слово" if cyrillic else "word"

    body_parts = [word] * body_words
    text = " ".join(body_parts)

    # Добавляем цифры
    for i in range(digits):
        text += f" 73{i}"

    # Добавляем <b> (буквенные пометки — чтобы не вносить лишние цифры)
    bold_words = ["важно", "ключевое", "главное", "критично", "необходимо", "запомни"]
    for i in range(bold):
        text += f" <b>{bold_words[i % len(bold_words)]}</b>"

    # Добавляем эмодзи
    text += " " + ("🚀 " * emoji)

    # Хэштеги (буквенные — чтобы цифры в них не ломали digits-проверку)
    tags = ["#ИИ", "#нейросети", "#технологии", "#бизнес", "#наука"]
    text += " " + " ".join(tags[:hashtags])

    if banned:
        text += f" {banned}"

    if link is None:
        text += f"\n\n{GOOD_LINK}"
    else:
        text += f"\n\n{link}"

    return text


class TestValidatePostHappy:
    def test_valid_post(self) -> None:
        post = _build_post()
        result = validate_post(post, article_url=URL)
        assert result.ok, f"unexpectedly invalid: {result.errors}"


class TestValidatePostErrors:
    def test_empty(self) -> None:
        result = validate_post("", article_url=URL)
        assert not result.ok
        assert "empty post" in result.errors

    def test_too_short(self) -> None:
        post = _build_post(body_words=10)
        result = validate_post(post, article_url=URL)
        assert not result.ok
        assert any("too short" in e for e in result.errors)

    def test_too_long(self) -> None:
        post = _build_post(body_words=500)
        result = validate_post(post, article_url=URL)
        assert not result.ok
        assert any("too long" in e for e in result.errors)

    def test_no_digits(self) -> None:
        post = _build_post(digits=0)
        result = validate_post(post, article_url=URL)
        assert not result.ok
        assert any("no digits" in e for e in result.errors)

    def test_no_bold(self) -> None:
        post = _build_post(bold=0)
        result = validate_post(post, article_url=URL)
        assert not result.ok
        assert any("too few <b>" in e for e in result.errors)

    def test_no_emoji(self) -> None:
        post = _build_post(emoji=0)
        result = validate_post(post, article_url=URL)
        assert not result.ok
        assert any("too few emoji" in e for e in result.errors)

    def test_no_hashtags(self) -> None:
        post = _build_post(hashtags=0)
        result = validate_post(post, article_url=URL)
        assert not result.ok
        assert any("no hashtags" in e for e in result.errors)

    def test_wrong_link(self) -> None:
        post = _build_post(link='<a href="https://wrong.com/x">читать</a>')
        result = validate_post(post, article_url=URL)
        assert not result.ok
        assert any("missing or wrong article link" in e for e in result.errors)

    def test_missing_link(self) -> None:
        post = _build_post(link="")
        result = validate_post(post, article_url=URL)
        assert not result.ok
        assert any("missing or wrong article link" in e for e in result.errors)

    def test_not_russian(self) -> None:
        post = _build_post(cyrillic=False)
        result = validate_post(post, article_url=URL, language="русский")
        assert not result.ok
        assert any("not enough Russian" in e for e in result.errors)

    def test_english_channel_passes_english_text(self) -> None:
        post = _build_post(cyrillic=False)
        result = validate_post(post, article_url=URL, language="english")
        # Не должно падать на кириллице — другой язык
        assert all("not enough Russian" not in e for e in result.errors)


class TestBannedWords:
    def test_blocks_revolyutsiya(self) -> None:
        post = _build_post(banned="революция в мире")
        result = validate_post(post, article_url=URL)
        assert not result.ok
        assert any("banned words" in e for e in result.errors)

    def test_blocks_proryv(self) -> None:
        post = _build_post(banned="это настоящий прорыв")
        result = validate_post(post, article_url=URL)
        assert not result.ok

    def test_blocks_water_phrase(self) -> None:
        post = _build_post(banned="в данной статье говорится")
        result = validate_post(post, article_url=URL)
        assert not result.ok


class TestWarnings:
    def test_many_emoji_only_warning(self) -> None:
        post = _build_post(emoji=20)
        result = validate_post(post, article_url=URL)
        # Слишком много эмодзи — warning, но не блокер
        assert result.ok
        assert any("too many emoji" in w for w in result.warnings)


class TestStats:
    def test_stats_populated(self) -> None:
        post = _build_post()
        result = validate_post(post, article_url=URL)
        assert result.stats["words"] >= MIN_WORDS
        assert result.stats["digits"] >= MIN_DIGITS
        assert result.stats["bold"] >= MIN_BOLD
        assert result.stats["emoji"] >= MIN_EMOJI
        assert "cyrillic_ratio" in result.stats
