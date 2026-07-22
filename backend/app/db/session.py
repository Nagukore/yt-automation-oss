"""SQLAlchemy engine + session factory.

The engine is created lazily so that importing any module in the app does not require
the Postgres driver to be installed or the database to be reachable. This keeps unit
tests, CLI tooling and `--help` fast and dependency-light.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import TYPE_CHECKING

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.logging import logger

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def _is_transaction_pooler(url: str) -> bool:
    """Detect a PgBouncer *transaction*-mode endpoint.

    Transaction pooling multiplexes many clients onto few server connections, so a
    connection isn't pinned to one client between statements. That breaks server-side
    prepared statements, which psycopg3 uses by default — the symptom is intermittent
    'prepared statement "_pg3_0" already exists' errors under concurrency.

    Port 6543 is transaction mode on both Supabase and Neon. Supabase's *session*
    pooler shares the same hostname but runs on 5432 and behaves like a normal
    connection, so hostname alone is not a reliable signal.
    """
    return ":6543" in url or "pgbouncer=true" in url.lower()


def get_engine() -> Engine:
    global _engine
    if _engine is not None:
        return _engine

    url = settings.database_url

    if settings.is_sqlite:
        # SQLite (local dev / tests) uses a different pool and needs check_same_thread
        # off because Celery/APScheduler touch it from threads.
        _engine = create_engine(url, connect_args={"check_same_thread": False}, future=True)
        return _engine

    connect_args: dict = {}
    kwargs: dict = {
        "pool_pre_ping": True,  # essential: hosted/serverless DBs drop idle connections
        "future": True,
    }

    if _is_transaction_pooler(url):
        # Disable psycopg3's prepared statements — PgBouncer transaction mode can't
        # support them. Keep the local pool small; the pooler does the real pooling.
        connect_args["prepare_threshold"] = None
        kwargs.update(pool_size=5, max_overflow=5, pool_recycle=300)
        logger.info("DB: transaction pooler detected — prepared statements disabled")
    else:
        # Direct connection (local Postgres, or Supabase/Neon direct endpoint).
        # Serverless tiers suspend when idle, so recycle connections rather than
        # handing out sockets the server has already closed.
        kwargs.update(pool_size=5, max_overflow=10, pool_recycle=1800)

    if connect_args:
        kwargs["connect_args"] = connect_args

    _engine = create_engine(url, **kwargs)
    return _engine


def get_sessionmaker() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_engine(), autoflush=False, autocommit=False, future=True
        )
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency."""
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()


class session_scope:
    """Context manager for non-request contexts (Celery tasks, scheduler).

    Commits on clean exit, rolls back on exception, always closes.
    """

    def __enter__(self) -> Session:
        self.db = get_sessionmaker()()
        return self.db

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type:
                self.db.rollback()
            else:
                self.db.commit()
        finally:
            self.db.close()
