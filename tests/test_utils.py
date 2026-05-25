"""Тесты bot.utils — канонизация URL и парсинг Telegram-ответа."""

from __future__ import annotations

import pytest

from bot.utils import best_telegram_file_id, canonicalize_url


class TestCanonicalizeUrl:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            # UTM-метки убираются
            (
                "https://example.com/article?utm_source=fb&utm_medium=cpc&id=123",
                "https://example.com/article?id=123",
            ),
            # Trailing slash
            ("https://Example.COM/article/", "https://example.com/article"),
            # Несколько UTM рядом
            ("https://news.com/post?id=1&utm_campaign=x", "https://news.com/post?id=1"),
            # fbclid
            ("https://a.com/page?fbclid=xx&p=1", "https://a.com/page?p=1"),
            # Корень не теряет /
            ("https://example.com/", "https://example.com/"),
            # Сортировка query
            ("https://example.com/?b=2&a=1", "https://example.com/?a=1&b=2"),
            # Только tracking → пустой query
            ("https://example.com/article?gclid=x&utm_source=y", "https://example.com/article"),
            # Yandex yclid
            ("https://example.com/?yclid=12345&id=1", "https://example.com/?id=1"),
            # Невалидный URL — возвращаем как есть
            ("not-a-url", "not-a-url"),
            # Пустая строка — пустая
            ("", ""),
        ],
    )
    def test_examples(self, raw: str, expected: str) -> None:
        assert canonicalize_url(raw) == expected

    def test_same_url_diff_utm_yields_same_canonical(self) -> None:
        """Одна и та же статья с разными UTM-метками = один canonical."""
        a = canonicalize_url("https://x.com/a?utm_source=fb&id=1")
        b = canonicalize_url("https://x.com/a?utm_source=tw&id=1")
        c = canonicalize_url("https://x.com/a?id=1&utm_medium=cpc")
        assert a == b == c

    def test_fragment_dropped(self) -> None:
        assert canonicalize_url("https://x.com/a#section") == "https://x.com/a"

    def test_default_ports_dropped(self) -> None:
        assert canonicalize_url("http://example.com:80/x") == "http://example.com/x"
        assert canonicalize_url("https://example.com:443/x") == "https://example.com/x"


class TestBestTelegramFileId:
    def test_picks_largest_by_file_size(self) -> None:
        resp = {
            "ok": True,
            "result": {
                "photo": [
                    {"file_id": "small", "file_size": 100},
                    {"file_id": "medium", "file_size": 5000},
                    {"file_id": "large", "file_size": 50000},
                ]
            },
        }
        assert best_telegram_file_id(resp) == "large"

    def test_empty_response(self) -> None:
        assert best_telegram_file_id({}) is None

    def test_error_response(self) -> None:
        assert best_telegram_file_id({"ok": False, "description": "x"}) is None

    def test_missing_photo_key(self) -> None:
        assert best_telegram_file_id({"ok": True, "result": {}}) is None

    def test_single_photo(self) -> None:
        resp = {"ok": True, "result": {"photo": [{"file_id": "abc", "file_size": 100}]}}
        assert best_telegram_file_id(resp) == "abc"

    def test_no_file_size_falls_back_to_zero(self) -> None:
        """Если file_size отсутствует, сортируем безопасно по 0."""
        resp = {
            "ok": True,
            "result": {
                "photo": [
                    {"file_id": "a"},  # default 0
                    {"file_id": "b", "file_size": 100},
                ]
            },
        }
        assert best_telegram_file_id(resp) == "b"
