"""Подключение к базе. PostgreSQL в проде и CI, SQLite — только для быстрых тестов."""
from __future__ import annotations

import os
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

DEFAULT_URL = "postgresql+asyncpg://grader:grader@localhost:5432/grader"


class Base(DeclarativeBase):
    pass


def database_url() -> str:
    return os.getenv("DATABASE_URL", DEFAULT_URL)


_engine = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def engine():
    global _engine, _sessionmaker
    if _engine is None:
        _engine = create_async_engine(database_url(), future=True, pool_pre_ping=True)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


async def get_session() -> AsyncIterator[AsyncSession]:
    engine()
    assert _sessionmaker is not None
    async with _sessionmaker() as session:
        yield session


async def reset_engine() -> None:
    """Закрыть движок и забыть его.

    Пул соединений привязан к тому event loop, в котором был создан. Тесты
    получают свой loop на каждый тест, поэтому переиспользованный движок
    отдаёт «Future attached to a different loop» — на PostgreSQL это падение,
    на SQLite молчит. Отсюда же следует, что в проде это звать не нужно:
    там loop один на всё время жизни процесса.
    """
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def create_all() -> None:
    """Схема создаётся из моделей.

    Осознанный размен: для сервиса такого размера миграции Alembic — лишний
    слой. Как только схема начнёт меняться на живых данных, это станет
    неверным решением и Alembic придётся завести.
    """
    from app import models  # noqa: F401  — модели должны быть импортированы

    async with engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
