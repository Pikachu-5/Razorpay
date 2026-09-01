from typing import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


def _build_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True, future=True)


engine: AsyncEngine = _build_engine(get_settings().database_url)

session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def ensure_runtime_schema() -> None:
    """Fail clearly when the checked-in migration chain has not been applied."""
    async with engine.connect() as connection:
        required = (
            "raw_events", "opportunities", "interventions", "durable_events",
            "detector_states",
        )
        rows = (
            await connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = ANY(:required)"
                ),
                {"required": list(required)},
            )
        ).scalars().all()
        missing = sorted(set(required) - set(rows))
        if missing:
            raise RuntimeError(
                "database schema is not initialized; run `python -m alembic upgrade head` "
                f"before starting the API (missing: {', '.join(missing)})"
            )
