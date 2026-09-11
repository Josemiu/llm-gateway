import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.services.database import Base


@pytest_asyncio.fixture(autouse=True)
async def usage_db(monkeypatch: pytest.MonkeyPatch):
    """Redirect all usage-tracking DB access to an in-memory SQLite DB.

    Autouse so no test can accidentally write to the real Postgres via the
    record_usage background task just by exercising a successful endpoint
    call - only tests that actually care about usage tracking need to
    reference this fixture's return value.

    Patches `app.services.database.async_session_factory` - the single
    canonical location - rather than each consumer module's own imported
    name. Consumers (usage_service, metrics_service, ...) must look it up
    via `database.async_session_factory()` at call time (not
    `from app.services.database import async_session_factory`), otherwise
    they'd bind their own local reference at import time and this patch
    would silently miss them (this bit us once already when
    metrics_service.py was added without updating this fixture).
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("app.services.database.async_session_factory", session_factory)

    yield session_factory

    await engine.dispose()
