"""SQLAlchemy engine + session for the decision-log persistence layer.

Best-effort, non-fatal: the trading pipeline must never break because the
decision log is unreachable. Callers use ``get_session()`` inside a context
manager and wrap writes in try/except.
"""

from __future__ import annotations

import logging

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.core.config import settings

log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _make_engine(dsn: str | None = None) -> Engine:
    url = dsn or settings.postgres_dsn
    return create_engine(url, pool_pre_ping=True, future=True)


_engine: Engine | None = None
_session_factory: sessionmaker | None = None


def get_engine() -> Engine:
    """Return the lazily-created (cached) engine."""
    global _engine
    if _engine is None:
        _engine = _make_engine()
    return _engine


def get_session_factory() -> sessionmaker:
    """Return the cached sessionmaker bound to the engine."""
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _session_factory


def get_session() -> Session:
    """Open a new Session. Use as a context manager; always close it."""
    return get_session_factory()()


def init_db() -> None:
    """Create all tables (idempotent). Safe to call at startup."""
    try:
        Base.metadata.create_all(bind=get_engine())
    except Exception as exc:  # noqa: BLE001
        log.warning("init_db failed (non-fatal): %s", exc)


def ping() -> bool:
    """Return True if the DB is reachable, False otherwise (never raises)."""
    try:
        with get_engine().connect() as conn:
            conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("db ping failed: %s", exc)
        return False
