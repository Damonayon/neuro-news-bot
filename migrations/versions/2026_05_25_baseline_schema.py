"""baseline schema — состояние БД на момент подключения Alembic (T1.7).

Создаёт все 7 таблиц из bot.models.Base. На существующих БД, где таблицы
уже есть (создавались через init_db() в T1.2-T1.5), эта миграция «штампуется»
как уже применённая: `alembic stamp head` — без реального CREATE TABLE.

Revision ID: e60fdf0030d4
Revises:
Create Date: 2026-05-25
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "e60fdf0030d4"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Создаёт все таблицы baseline-схемы.

    Используем Base.metadata.create_all (single source of truth — bot/models.py),
    чтобы миграция автоматически отражала актуальные модели в момент применения.
    Это норма для самой первой baseline-миграции; последующие миграции пишутся
    явно через op.create_table / op.add_column и т.д.
    """
    # Локальный импорт, чтобы Alembic не падал при загрузке версионных файлов
    # без настроенного env (например, на этапе сборки docs).
    from bot.models import Base

    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    """Полный сброс схемы (используется крайне редко — обычно бэкап важнее)."""
    from bot.models import Base

    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
