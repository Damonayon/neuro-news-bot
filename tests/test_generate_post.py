"""Тесты scripts.generate_post — парсинг ответов модели, ensure_correct_link, рубрики."""

from __future__ import annotations

import pytest

from scripts.generate_post import (
    _extract_json,
    detect_rubric,
    ensure_correct_link,
    parse_post,
)


class TestExtractJson:
    def test_clean_json(self) -> None:
        assert _extract_json('{"a": 1}') == {"a": 1}

    def test_json_in_markdown(self) -> None:
        raw = '```json\n{"a": 1, "b": "x"}\n```'
        assert _extract_json(raw) == {"a": 1, "b": "x"}

    def test_json_no_lang_marker(self) -> None:
        raw = '```\n{"a": 1}\n```'
        assert _extract_json(raw) == {"a": 1}

    def test_extra_text_around_block(self) -> None:
        raw = 'Here you go: ```json\n{"a": 1}\n``` Enjoy!'
        assert _extract_json(raw) == {"a": 1}


class TestParsePost:
    def test_valid_russian(self) -> None:
        raw = '{"post": "Это пост на русском с большим количеством кириллицы для проверки", "image_prompt": "test"}'
        text, prompt = parse_post(raw)
        assert "русском" in text
        assert prompt == "test"

    def test_rejects_too_few_cyrillic(self) -> None:
        raw = '{"post": "This is English post about AI", "image_prompt": "test"}'
        with pytest.raises(ValueError, match="не на русском"):
            parse_post(raw)

    def test_rejects_empty_post(self) -> None:
        with pytest.raises(ValueError, match="Пустой"):
            parse_post('{"post": "", "image_prompt": "x"}')

    def test_default_image_prompt(self) -> None:
        raw = '{"post": "Это пост на русском с большим количеством кириллицы для проверки", "image_prompt": ""}'
        _, prompt = parse_post(raw)
        assert prompt  # дефолтный непустой


class TestEnsureCorrectLink:
    def test_adds_link_when_missing(self) -> None:
        post = "Текст поста"
        result = ensure_correct_link(post, "https://x.com/a")
        assert '<a href="https://x.com/a">📖 Читать полностью</a>' in result

    def test_keeps_correct_link(self) -> None:
        post = 'Текст\n\n<a href="https://x.com/a">📖 Читать полностью</a>'
        result = ensure_correct_link(post, "https://x.com/a")
        # Не должно задвоиться
        assert result.count('<a href="https://x.com/a">') == 1

    def test_replaces_wrong_link(self) -> None:
        post = 'Текст\n\n<a href="https://wrong.com/y">📖 Читать полностью</a>'
        result = ensure_correct_link(post, "https://x.com/a")
        assert 'href="https://x.com/a"' in result
        assert 'href="https://wrong.com/y"' not in result


class TestDetectRubric:
    @pytest.mark.parametrize(
        ("title", "summary", "expected_emoji"),
        [
            ("OpenAI launches new model", "", "🚀"),
            ("Компания представила новый продукт", "", "🚀"),
            ("Google уволил 12000 сотрудников", "", "🔻"),
            ("Microsoft fired 10000 employees", "", "🔻"),
            ("Study shows 73% use ChatGPT", "", "📊"),
            ("Новое исследование: ИИ работает", "", "📊"),
            ("Best AI tool for developers", "popular service for everyone", "🔧"),
            ("Stripe raised $1 billion in funding", "", "💰"),
            ("Random news headline", "Some other content", "🤖"),
        ],
    )
    def test_keyword_routing(self, title: str, summary: str, expected_emoji: str) -> None:
        rubric = detect_rubric({"title": title, "summary": summary})
        assert rubric.startswith(expected_emoji)
