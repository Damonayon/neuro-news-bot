"""bot — общий пакет для скриптов сети Telegram-каналов.

Содержит:
- config: валидация env-переменных (pydantic-settings)
- models: ORM-схема БД (SQLAlchemy 2.0)
- db: движок и фабрика сессий
- storage: высокоуровневые операции (upsert статьи, очередь постов и т.д.)
"""

__version__ = "0.2.0"
