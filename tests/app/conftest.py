"""
Фикстуры чата: ОДИН набор контрактных тестов прогоняется против обеих реализаций.
Postgres поднимается в Docker через testcontainers (один контейнер на сессию тестов),
JSON — во временной папке.
"""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.chat.repositories.json_repo import JsonChatRepository
from app.chat.repositories.pg_models import Base
from app.chat.repositories.pg_repo import PostgresChatRepository


@pytest.fixture(scope="session")
def pg_url():
    try:
        from testcontainers.community.postgres import PostgresContainer
    except ImportError:  # старые версии testcontainers
        from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:17-alpine", driver="asyncpg") as pg:
        yield pg.get_connection_url()


@pytest.fixture(params=["json", "postgres"])
async def repo(request, tmp_path):
    if request.param == "json":
        yield JsonChatRepository(base_dir=tmp_path)
        return
    url = request.getfixturevalue("pg_url")
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield PostgresChatRepository(session)
    await engine.dispose()
