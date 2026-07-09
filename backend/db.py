"""Async SQLAlchemy engine + session factory for SQLite persistence.

Sources the DB URL from `get_settings()` (never a hardcoded literal) so
tests can point at a throwaway file via the `DATABASE_URL` env var. Enables
`PRAGMA foreign_keys=ON` (SQLAlchemy doesn't do this by default for SQLite)
and `PRAGMA journal_mode=WAL` (lets concurrent readers proceed alongside a
single writer, avoiding "database is locked" under overlapping
policy-read/audit-write coroutines) on every new connection. `check_same_thread`
is not needed — aiosqlite manages its own thread internally.
"""

from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import get_settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(get_settings().database_url, connect_args={"timeout": 15})
async_session = async_sessionmaker(engine, expire_on_commit=False)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


async def init_models() -> None:
    """No Alembic this milestone (local dev, single SQLite file) — just
    create any missing tables against the current models."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
