"""Health check endpoint."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    version: str


class RootResponse(BaseModel):
    """Root status response."""

    service: str
    status: str
    version: str
    timestamp: str
    health_url: str
    docs_url: str


@router.get("/", response_model=RootResponse, include_in_schema=False)
async def home() -> RootResponse:
    """Root API status endpoint."""
    from runtm_api import __version__

    timestamp = datetime.now(UTC).isoformat(timespec="seconds")

    return RootResponse(
        service="runtm-api",
        status="healthy",
        version=__version__,
        timestamp=timestamp,
        health_url="/health",
        docs_url="/docs",
    )


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint.

    Returns 200 OK if the service is healthy.
    """
    from runtm_api import __version__

    return HealthResponse(
        status="healthy",
        version=__version__,
    )
