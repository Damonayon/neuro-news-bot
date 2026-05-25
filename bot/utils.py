"""bot.utils — мелкие переиспользуемые утилиты.

- canonicalize_url: нормализация URL для устойчивой дедупликации
- best_telegram_file_id: достать самый большой file_id из ответа Telegram
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Tracking-параметры, которые меняют URL, но не меняют контент.
# Список консервативный — добавляем только то, что точно tracking.
TRACKING_PARAMS: frozenset[str] = frozenset(
    {
        # UTM-семейство
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "utm_name",
        # Facebook / Meta
        "fbclid",
        "fb_action_ids",
        "fb_action_types",
        "fb_source",
        # Google / Ads
        "gclid",
        "gclsrc",
        "dclid",
        "_ga",
        "_gl",
        # Yandex
        "yclid",
        "ymclid",
        "etext",
        # Mailing
        "mc_cid",
        "mc_eid",
        # Прочие распространённые
        "ref",
        "ref_src",
        "ref_url",
        "source",
        "referrer",
        "campaign_id",
        "campaign",
        "mkt_tok",
        "spm",
    }
)


def canonicalize_url(url: str) -> str:
    """Нормализует URL для дедупликации.

    Применяет:
    - lowercase scheme и host
    - убирает port для http(80)/https(443)
    - убирает tracking-параметры (utm_*, fbclid, gclid и т.п.)
    - убирает fragment (#section)
    - сортирует оставшиеся query-параметры
    - убирает trailing slash в path (кроме корня "/")

    Если URL невалидный — возвращает исходную строку (защита от падений).
    """
    if not url:
        return url
    try:
        parts = urlsplit(url.strip())
        if not parts.scheme:
            return url  # типа "javascript:..." или мусор — не трогаем

        scheme = parts.scheme.lower()
        netloc = parts.netloc.lower()

        # Уберём дефолтный порт
        if (scheme == "http" and netloc.endswith(":80")) or (
            scheme == "https" and netloc.endswith(":443")
        ):
            netloc = netloc.rsplit(":", 1)[0]

        # Чистим query
        kept_query = [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in TRACKING_PARAMS
        ]
        kept_query.sort(key=lambda kv: kv[0])
        query = urlencode(kept_query, doseq=True)

        path = parts.path
        if len(path) > 1 and path.endswith("/"):
            path = path.rstrip("/")

        return urlunsplit((scheme, netloc, path, query, ""))
    except Exception:
        # Любая необычная форма URL — возвращаем как есть
        return url


def best_telegram_file_id(send_photo_response: dict[str, Any]) -> str | None:
    """Достаёт file_id максимального размера из ответа Telegram sendPhoto.

    Структура успешного ответа:
        {"ok": True, "result": {"photo": [{file_id, file_size, width, height}, ...]}}

    Возвращает file_id с максимальным file_size, либо None если структура неожиданная.
    """
    try:
        photos: Iterable[dict[str, Any]] = send_photo_response["result"]["photo"]
        sized = sorted(
            (p for p in photos if "file_id" in p),
            key=lambda p: p.get("file_size", 0),
            reverse=True,
        )
        if sized:
            file_id = sized[0]["file_id"]
            return str(file_id) if file_id is not None else None
    except (KeyError, TypeError):
        pass
    return None
