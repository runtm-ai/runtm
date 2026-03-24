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
            pool_recycle=1800,
            connect_args={"connect_timeout": 5},
        )
        logger.info("Created SQLAlchemy engine (pool_size=5, max_overflow=10)")
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
