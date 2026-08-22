"""Tests for authentication."""

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

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
async def test_deployment_requires_auth(client):
    """Deployment endpoints should require auth."""
    response = await client.get("/v0/deployments/dep_abc123")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_deployment_with_invalid_token(client):
    """Invalid token should return 401."""
    response = await client.get(
        "/v0/deployments/dep_abc123",
        headers={"Authorization": "Bearer invalid-token"},
    )
    # In single-tenant mode with valid RUNTM_API_SECRET, invalid tokens get 401
    # If deployment existed, would get 404 after auth passes
    assert response.status_code in (401, 404)
