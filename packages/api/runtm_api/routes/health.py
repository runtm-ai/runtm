"""Health check endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from runtm_api.db.session import get_db

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Lightweight health check for load-balancer probes.

    No I/O -- returns immediately.
    """
    from runtm_api import __version__

    return HealthResponse(
        status="healthy",
        version=__version__,
    )


@router.get("/health/detailed")
def detailed_health_check(db: Session = Depends(get_db)) -> dict[str, Any]:
    """Detailed health check that probes Postgres, Redis, and the worker queue.

    Use ``GET /health`` for fast load-balancer probes; this endpoint
    performs real I/O and may take a few hundred milliseconds.
    """
    from runtm_api import __version__

    checks: dict[str, Any] = {
        "postgres": "ok",
        "redis": "ok",
        "worker_queue_depth": 0,
    }
    overall = "healthy"

    # -- Postgres --
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        checks["postgres"] = f"error: {exc}"
        overall = "degraded"

    # -- Redis + RQ queue depth --
    try:
        import os

        from redis import Redis
        from rq import Queue

        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        redis_conn = Redis.from_url(redis_url, socket_connect_timeout=3)
        redis_conn.ping()

        queue = Queue("deployments", connection=redis_conn)
        checks["worker_queue_depth"] = len(queue)
    except Exception as exc:
        checks["redis"] = f"error: {exc}"
        overall = "degraded"

    return {
        "status": overall,
        "version": __version__,
        "checks": checks,
    }
