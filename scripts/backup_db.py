"""scripts/backup_db.py — ежедневный бэкап БД в отдельную ветку.

Логика:
  1. Сжать `data/bot.db` в gzip.
  2. Положить в каталог `backups/` под именем `YYYY-MM-DD.db.gz`.
  3. Удалить файлы старше BACKUP_RETENTION_DAYS.

Сам коммит и push выполняет workflow (см. .github/workflows/backup.yml),
который работает в orphan-ветке `backups` — это не засоряет main историю.

Сам файл идемпотентен: повторный запуск в тот же день перезапишет дневной архив.
"""

from __future__ import annotations

import gzip
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bot.config import DATA_DIR  # noqa: E402
from bot.logging_setup import get_logger, setup_logging  # noqa: E402


BACKUPS_DIR = PROJECT_ROOT / "backups"
DB_PATH = DATA_DIR / "bot.db"
BACKUP_RETENTION_DAYS = 30


log = get_logger("backup_db")


def make_backup() -> Path:
    """Создаёт сжатый бэкап БД. Возвращает путь к gzip-файлу."""
    if not DB_PATH.exists():
        raise FileNotFoundError(f"БД не найдена: {DB_PATH}")

    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    target = BACKUPS_DIR / f"{today}.db.gz"

    # Перезапись OK — это «снимок дня»
    with DB_PATH.open("rb") as src, gzip.open(target, "wb", compresslevel=6) as dst:
        shutil.copyfileobj(src, dst)

    size_kb = target.stat().st_size / 1024
    log.info("Создан бэкап %s (%.1f KB)", target.name, size_kb)
    return target


def cleanup_old_backups() -> int:
    """Удаляет бэкапы старше BACKUP_RETENTION_DAYS дней. Возвращает количество удалённых."""
    if not BACKUPS_DIR.exists():
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=BACKUP_RETENTION_DAYS)
    removed = 0
    for f in BACKUPS_DIR.glob("*.db.gz"):
        try:
            # Имя файла = дата; парсим её как авторитетный источник возраста
            stem = f.name.replace(".db.gz", "")
            d = datetime.strptime(stem, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if d < cutoff:
                f.unlink()
                removed += 1
                log.info("Удалён старый бэкап: %s", f.name)
        except ValueError:
            # Неизвестное имя — не трогаем
            continue
    return removed


def main() -> None:
    setup_logging()
    log.info("=== Backup БД — %s ===", datetime.now().strftime("%Y-%m-%d %H:%M"))

    make_backup()
    removed = cleanup_old_backups()
    if removed:
        log.info("Очищено старых бэкапов: %d", removed)

    log.info("Готово")


if __name__ == "__main__":
    main()
