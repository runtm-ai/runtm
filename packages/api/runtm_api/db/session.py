"""Database session management for Runtm API.

Engine and session factory are module-level singletons so that all
requests share a single connection pool instead of creating a new
engine (and pool) per request -- which previously exhausted
PostgreSQL's max_connections under load.
"""

from __future__ import annotations

import logging
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from runtm_api.core.config import get_settings

logger = logging.getLogger(__name__)

_engine = None
_session_factory: sessionmaker | None = None


def get_engine():
    """Return the process-global engine, creating it on first call."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            pool_timeout=30,
            # Recycle well below the upstream pooler's idle timeout. In prod the DB
            # sits behind Fly MPG's PgBouncer, which closes idle server connections
            # long before the previous 1800s -- ~1/3 of pre-ping probes were hitting
            # dead connections ("SSL connection has been closed unexpectedly").
            pool_recycle=300,
            pool_use_lifo=True,
            connect_args={
                "connect_timeout": 5,
                # TCP keepalives so the kernel notices half-open connections that
                # the pooler/network dropped, instead of failing on next use.
                "keepalives": 1,
                "keepalives_idle": 30,
                "keepalives_interval": 10,
                "keepalives_count": 3,
            },
        )
        logger.info("Created SQLAlchemy engine (pool_size=5, max_overflow=10, lifo=True)")
    return _engine


def get_session_factory() -> sessionmaker:
    """Return the process-global session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(),
            autocommit=False,
            autoflush=False,
        )
    return _session_factory


def get_db() -> Generator[Session, None, None]:
    """Dependency for getting database session.

    Usage:
        @router.get("/")
        def endpoint(db: Session = Depends(get_db)):
            ...
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.close()


def create_session() -> Session:
    """Create a new database session for non-FastAPI contexts (e.g., worker).

    Remember to close the session when done:
        session = create_session()
        try:
            # use session
        finally:
            session.close()
    """
    factory = get_session_factory()
    return factory()
