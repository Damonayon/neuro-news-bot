"""scripts/health_check.py — Health-check всей системы.

Запускается cron'ом каждые 30 минут (см. .github/workflows/health.yml).
Проверяет ключевые компоненты, чистит мусор и обновляет статус системы в БД.

Что проверяется:
  1. БД — подключение и базовый SELECT.
  2. Telegram Bot API — getMe.
  3. GitHub Models API — достижимость хоста.
  4. RSS-источники — хотя бы 50% отвечают 200.
  5. Очередь pending — нет постов старше PENDING_TTL_HOURS (если есть, чистим).
  6. Таблица logs — если > LOG_TABLE_MAX_ROWS, удаляем старейшие.

Алерт в Telegram отправляется только при переходе OK → FAIL (анти-спам).
Если ничего не сломано — workflow завершается зелёным и тихо.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import requests  # noqa: E402

from bot.config import get_settings  # noqa: E402
from bot.db import init_db, session_scope  # noqa: E402
from bot.http import (  # noqa: E402
    CircuitOpenError,
    DeadlineExceededError,
    http_get,
    set_deadline,
)
from bot.logging_setup import get_logger, setup_logging  # noqa: E402
from bot.models import LogEntry, Post, POST_STATUS_FAILED, POST_STATUS_PENDING  # noqa: E402
from bot.storage import ensure_channel, get_state, set_state  # noqa: E402

from sqlalchemy import delete, select, update, func  # noqa: E402


log = get_logger("health_check")
settings = get_settings()

# ─── Параметры ───────────────────────────────────────────────────────────────
HEALTHCHECK_DEADLINE_SEC = 60
PENDING_TTL_HOURS = 48
LOG_TABLE_MAX_ROWS = 10_000
LOG_RETENTION_DAYS = 30
MIN_RSS_OK_RATIO = 0.5  # минимум 50% RSS-фидов должны отвечать


# ─── Отдельные проверки ──────────────────────────────────────────────────────


def check_db() -> tuple[bool, str]:
    try:
        with session_scope() as session:
            session.execute(select(func.count()).select_from(Post.__table__))
        return True, "ok"
    except Exception as exc:
        return False, f"DB error: {exc}"


def check_telegram() -> tuple[bool, str]:
    try:
        resp = http_get(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/getMe",
            timeout=10,
        )
        data = resp.json()
        if not data.get("ok"):
            return False, f"telegram getMe ok=false: {data}"
        return True, f"bot={data['result'].get('username')}"
    except (requests.RequestException, CircuitOpenError, DeadlineExceededError) as exc:
        return False, f"telegram unreachable: {exc}"


def check_github_models() -> tuple[bool, str]:
    """Лёгкая проверка достижимости хоста (без траты квот).
    Реальный API требует POST с токеном; проверим 4xx/5xx по корню."""
    try:
        # На корень эндпоинт отвечает 404 — это нормально, нам важна сама связность.
        resp = http_get(
            "https://models.inference.ai.azure.com/", timeout=10
        )
        # 200/401/403/404 — хост жив; 5xx — проблемы
        if resp.status_code >= 500:
            return False, f"github models HTTP {resp.status_code}"
        return True, f"reachable (HTTP {resp.status_code})"
    except (requests.RequestException, CircuitOpenError, DeadlineExceededError) as exc:
        return False, f"github models unreachable: {exc}"


def check_rss() -> tuple[bool, str]:
    feeds = settings.rss_feeds
    if not feeds:
        return True, "no feeds configured"
    ok = 0
    errors: list[str] = []
    for url in feeds:
        try:
            resp = http_get(url, timeout=10)
            if resp.status_code == 200:
                ok += 1
            else:
                errors.append(f"{url} → HTTP {resp.status_code}")
        except Exception as exc:
            errors.append(f"{url} → {type(exc).__name__}")
    ratio = ok / len(feeds)
    msg = f"{ok}/{len(feeds)} OK"
    if ratio >= MIN_RSS_OK_RATIO:
        return True, msg
    return False, f"{msg}; first errors: {'; '.join(errors[:3])}"


# ─── Чистка ──────────────────────────────────────────────────────────────────


def cleanup_stale_pending() -> int:
    """Помечает FAILED все pending-посты старше PENDING_TTL_HOURS."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=PENDING_TTL_HOURS)
    with session_scope() as session:
        stmt = (
            update(Post)
            .where(
                Post.status == POST_STATUS_PENDING,
                Post.created_at < cutoff,
            )
            .values(status=POST_STATUS_FAILED, decided_at=datetime.now(timezone.utc))
        )
        result = session.execute(stmt)
        return result.rowcount or 0


def cleanup_old_logs() -> int:
    """Удаляет старые логи (старше LOG_RETENTION_DAYS) ИЛИ если таблица больше лимита."""
    deleted = 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOG_RETENTION_DAYS)
    with session_scope() as session:
        total = session.execute(select(func.count()).select_from(LogEntry.__table__)).scalar() or 0

        if total > LOG_TABLE_MAX_ROWS:
            # Жёсткая чистка по возрасту
            r = session.execute(delete(LogEntry).where(LogEntry.created_at < cutoff))
            deleted = r.rowcount or 0

            # Если всё равно много — оставляем только последние LOG_TABLE_MAX_ROWS
            total_after = (
                session.execute(select(func.count()).select_from(LogEntry.__table__)).scalar() or 0
            )
            if total_after > LOG_TABLE_MAX_ROWS:
                # Хвост, который нужно удалить
                excess = total_after - LOG_TABLE_MAX_ROWS
                ids_to_delete = list(
                    session.execute(
                        select(LogEntry.id).order_by(LogEntry.id.asc()).limit(excess)
                    ).scalars()
                )
                if ids_to_delete:
                    r = session.execute(delete(LogEntry).where(LogEntry.id.in_(ids_to_delete)))
                    deleted += r.rowcount or 0
    return deleted


# ─── Главная функция ─────────────────────────────────────────────────────────


CHECKS: dict[str, Callable[[], tuple[bool, str]]] = {
    "db": check_db,
    "telegram": check_telegram,
    "github_models": check_github_models,
    "rss": check_rss,
}


def main() -> None:
    setup_logging()
    set_deadline(HEALTHCHECK_DEADLINE_SEC)
    init_db()

    log.info("=== Health-check [%s] — %s ===",
             settings.channel_topic, datetime.now().strftime("%Y-%m-%d %H:%M"))

    # Обеспечим существование канала, чтобы метаданные были консистентны
    with session_scope() as session:
        ensure_channel(session)

    results: dict[str, tuple[bool, str]] = {}
    for name, fn in CHECKS.items():
        try:
            ok, msg = fn()
        except Exception as exc:
            ok, msg = False, f"check raised: {exc}"
        results[name] = (ok, msg)
        log.info("  [%s] %s — %s", name, "OK" if ok else "FAIL", msg)

    # Чистка
    stale = cleanup_stale_pending()
    if stale:
        log.warning("Помечено как FAILED %d просроченных pending-постов (>%dч)",
                    stale, PENDING_TTL_HOURS)

    deleted_logs = cleanup_old_logs()
    if deleted_logs:
        log.info("Удалено старых записей logs: %d", deleted_logs)

    all_ok = all(ok for ok, _ in results.values())
    now_iso = datetime.now(timezone.utc).isoformat()

    # Сохраняем статус и определяем, нужен ли алерт (только при переходе OK→FAIL)
    with session_scope() as session:
        prev_status = get_state(session, "last_health_check_status")
        set_state(session, "last_health_check_at", now_iso)
        set_state(session, "last_health_check_status", "OK" if all_ok else "FAIL")

    if not all_ok:
        failed = [n for n, (ok, _) in results.items() if not ok]
        log.error("Health-check FAIL: %s", ", ".join(failed))
        # Если предыдущий статус был OK — это переход в FAIL → log.error уже
        # сработает на Telegram-алерт через bot.logging_setup. Если статус уже
        # был FAIL — TelegramAlertHandler сам дросселирует (10 мин на ключ).
        if prev_status == "OK":
            log.error("Health transition OK → FAIL")
        sys.exit(1)

    log.info("Health-check: всё OK")


if __name__ == "__main__":
    main()
