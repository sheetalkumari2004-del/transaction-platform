import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()

if os.environ.get("TESTING"):
    # pytest-asyncio gives each test function its own event loop by
    # default. A pooled connection (asyncpg) is bound to the loop that
    # created it, so reusing one across tests raises "attached to a
    # different loop". NullPool opens a fresh connection per checkout and
    # closes it on checkin -- no connection ever outlives a single test's
    # loop. Production keeps normal pooling; this only applies when the
    # test suite sets TESTING=1 (see tests/_db_fixtures.py).
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(settings.database_url, poolclass=NullPool, pool_pre_ping=True, echo=False)
else:
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
        echo=False,
    )

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
