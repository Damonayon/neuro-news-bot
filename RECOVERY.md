# RECOVERY.md — Восстановление и миграции БД

Короткий и практичный гайд: что делать, когда что-то пошло не так.

---

## 🗂 Где хранятся бэкапы

В **отдельной orphan-ветке `backups`** репозитория. По одному файлу на день:

```
backups/
├── 2026-05-23.db.gz
├── 2026-05-24.db.gz
└── 2026-05-25.db.gz
```

Хранение — **30 дней**, старше — удаляются автоматически. Создаётся через workflow `💾 Backup БД` (cron 03:30 UTC, см. [.github/workflows/backup.yml](.github/workflows/backup.yml)).

---

## 🔧 Восстановить БД из бэкапа

### Через GitHub UI (без терминала)

1. Открой репозиторий → переключи Branch в **`backups`** (верхний-левый dropdown).
2. Зайди в каталог `backups/` → выбери нужный день → нажми **Download raw file**.
3. Получи `.db.gz` → распакуй любым архиватором → получишь `bot.db`.
4. Положи в `data/bot.db` (заменив текущий) и закоммить в `main`.

### Через терминал

```bash
# Достать снимок за нужный день из ветки backups
git fetch origin backups
git show origin/backups:backups/2026-05-25.db.gz > /tmp/backup.db.gz

# Распаковать и заменить
gunzip -c /tmp/backup.db.gz > data/bot.db

# Закоммитить восстановленную БД в main
git add data/bot.db
git commit -m "🔄 Восстановление БД из бэкапа 2026-05-25"
git push
```

---

## 🛠 Изменение схемы БД (миграции через Alembic)

Когда нужно добавить колонку / таблицу / индекс, **не правь БД руками** — используй Alembic.

### 1. Изменил модель в `bot/models.py`

Например, добавил `Post.summary: Mapped[str]`.

### 2. Сгенерируй миграцию

```bash
alembic revision -m "add post summary" --autogenerate
```

Alembic создаст файл в `migrations/versions/2026_05_25_add_post_summary.py` со сравнением старой и новой схемы.

### 3. Просмотри сгенерированный файл

Иногда autogenerate ошибается с типами / индексами. Поправь руками если нужно.

### 4. Применить

```bash
alembic upgrade head
```

### 5. Откатить (если что-то пошло не так)

```bash
alembic downgrade -1
```

---

## 🧪 Проверить текущее состояние Alembic

```bash
alembic current        # какая ревизия сейчас применена
alembic history        # все миграции
alembic check          # есть ли изменения в моделях без миграции
```

---

## 🔄 Если БД вообще нет (свежий форк)

При первом запуске любого скрипта (`generate_post.py`, `check_approvals.py`, ...) вызывается `bot.db.init_db()`, который:

1. Создаёт все таблицы через `Base.metadata.create_all`
2. Автоматически проставляет `alembic stamp head` — БД считается актуальной по миграциям

Дополнительных команд не требуется.

---

## ⚠️ Если БД повреждена и нет бэкапа

В крайнем случае можно начать с чистого листа:

```bash
rm -f data/bot.db
python -c "from bot.db import init_db; init_db()"
```

Это создаст пустую БД со всеми таблицами. **Все статьи и pending-посты будут потеряны** — поэтому сначала всегда смотри в ветку `backups`.

---

## 📍 Файлы по теме

- `alembic.ini` — конфиг Alembic
- `migrations/env.py` — окружение Alembic (использует `bot.config` для URL)
- `migrations/versions/*.py` — все миграции
- `scripts/backup_db.py` — скрипт ежедневного бэкапа
- `.github/workflows/backup.yml` — cron 03:30 UTC
