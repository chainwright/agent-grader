"""Интеграционные тесты идут через настоящий HTTP-слой приложения.

DATABASE_URL берётся из окружения: в CI это PostgreSQL (тот же движок, что в
проде), локально — SQLite, чтобы прогон не требовал поднятой базы. Разница
между движками реальна, поэтому итоговая проверка — всегда зелёный CI.
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test_grader.db")


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def client():
    from httpx import ASGITransport, AsyncClient

    from app import db
    from app.main import app

    # чистая схема на каждый тест: остатки чужих сабмишенов ломают статистику
    await db.create_all()
    async with db.engine().begin() as conn:
        await conn.run_sync(db.Base.metadata.drop_all)
        await conn.run_sync(db.Base.metadata.create_all)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
