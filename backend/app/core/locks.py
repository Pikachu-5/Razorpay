"""Postgres transaction locks for singleton scheduled monitor work."""

from collections.abc import Awaitable, Callable
from typing import TypeVar

from sqlalchemy import text

from app.database.session import session_factory

T = TypeVar("T")


async def run_with_singleton_lock(name: str, work: Callable[[], Awaitable[T]]) -> T | None:
    """Run work once across replicas; the lock is released with this transaction."""
    async with session_factory() as session:
        acquired = (
            await session.execute(text("SELECT pg_try_advisory_xact_lock(hashtext(:name))"), {"name": name})
        ).scalar_one()
        if not acquired:
            return None
        result = await work()
        await session.commit()
        return result
