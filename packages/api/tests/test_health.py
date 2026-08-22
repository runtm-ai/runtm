"""Tests for health endpoint."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from runtm_api import __version__
from runtm_api.main import app


@pytest_asyncio.fixture
async def client():
    """Create test client."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as test_client:
        yield test_client


@pytest.mark.asyncio
async def test_health_returns_200(client):
    """Health endpoint should return 200."""
    response = await client.get("/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_health_returns_status(client):
    """Health endpoint should return status."""
    response = await client.get("/health")
    data = response.json()
    assert data["status"] == "healthy"
    assert "version" in data


@pytest.mark.asyncio
async def test_home_returns_json_status(client):
    """Root endpoint should return an API-style JSON status."""
    response = await client.get("/")
    data = response.json()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert data["service"] == "runtm-api"
    assert data["status"] == "healthy"
    assert data["version"] == __version__
    assert "timestamp" in data
    assert data["health_url"] == "/health"
    assert data["docs_url"] == "/docs"
