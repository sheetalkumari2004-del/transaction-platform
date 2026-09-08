"""
Integration/API/concurrency tests need real Postgres + Redis (per
assignment section 25, "integration tests with real instances").

Run:
    docker compose -f docker-compose.test.yml up -d
    TEST_DATABASE_URL=postgresql+asyncpg://txn_user:txn_pass@localhost:5433/txn_platform_test \
    TEST_REDIS_URL=redis://localhost:6380/0 \
    pytest tests/

Unit tests (tests/unit) have no such dependency and run anywhere.
"""

import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("DATABASE_URL", os.environ.get("TEST_DATABASE_URL", "postgresql+asyncpg://txn_user:txn_pass@localhost:5433/txn_platform_test"))
os.environ.setdefault("REDIS_URL", os.environ.get("TEST_REDIS_URL", "redis://localhost:6380/0"))
os.environ.setdefault("TESTING", "1")

from app.core.security import hash_api_key  # noqa: E402
from app.db.session import AsyncSessionLocal, Base, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models.client import Client  # noqa: E402



@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def _create_schema():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session(_create_schema):
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def test_client_and_key(db_session):
    # Unique per invocation -- data persists across tests within a
    # session (schema is created once), so a hardcoded key would collide
    # on the second test that requests this fixture.
    import uuid

    raw_key = f"tp_test_{uuid.uuid4().hex}"
    client = Client(name="pytest-client", api_key_hash=hash_api_key(raw_key))
    db_session.add(client)
    await db_session.commit()
    await db_session.refresh(client)
    return client, raw_key


@pytest_asyncio.fixture
async def api_client(_create_schema):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
