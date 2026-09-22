"""A redeploy into a live build must be refused, not turned into a new app.

Rain, 2026-09-21: `runtm deploy` while the previous deployment was still
building created a brand-new Fly app and a second concurrent build on the
shared builder; both then starved and timed out.
"""

from types import SimpleNamespace

from runtm_api.routes.deployments import in_flight_conflict
from runtm_shared.types import DeploymentState


def _dep(state):
    return SimpleNamespace(
        name="rain-wallets-proto", version=30, state=state, deployment_id="dep_9c6b8b665d32"
    )


def test_building_latest_is_a_409_conflict():
    c = in_flight_conflict(_dep(DeploymentState.BUILDING), force_new=False)
    assert c["code"] == "deployment_in_progress"
    assert c["deployment_id"] == "dep_9c6b8b665d32"
    assert "still building" in c["error"]


def test_queued_and_deploying_are_conflicts_too():
    for st in (DeploymentState.QUEUED, DeploymentState.DEPLOYING):
        assert in_flight_conflict(_dep(st), force_new=False) is not None


def test_terminal_states_redeploy_as_before():
    for st in (DeploymentState.READY, DeploymentState.FAILED):
        assert in_flight_conflict(_dep(st), force_new=False) is None


def test_new_flag_still_opts_out():
    assert in_flight_conflict(_dep(DeploymentState.BUILDING), force_new=True) is None


# ---------------------------------------------------------------------------
# Route-level: POST /v0/deployments with storage/queue/db stubbed (Guardian on
# runtm#73 asked for the admission path itself, not just the predicate).
# ---------------------------------------------------------------------------
import io
import zipfile
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

MANIFEST = b"name: rain-wallets-proto\ntemplate: docker\nport: 8080\n"


def _zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Dockerfile", "FROM scratch\n")
    return buf.getvalue()


def _latest(state):
    d = MagicMock()
    d.name, d.version, d.state, d.deployment_id = "rain-wallets-proto", 30, state, "dep_9c6b8b665d32"
    d.is_latest = True
    return d


@pytest.fixture
def harness(monkeypatch):
    from runtm_api.auth import get_auth_context
    from runtm_api.core.config import get_settings
    from runtm_api.db import get_db
    from runtm_api.routes import deployments_router
    from runtm_api.services import policy as policy_mod
    from runtm_shared import redis as redis_mod
    from runtm_shared import storage as storage_mod
    from runtm_shared.types import AuthContext

    db = MagicMock()
    q = db.query.return_value.filter.return_value
    q.scalar.return_value = 29  # max version so far
    q.order_by.return_value.with_for_update.return_value.first.return_value = None

    def _stamp(obj):  # a real INSERT would fill these; from_db needs them
        now = datetime.now(UTC)
        obj.created_at = obj.updated_at = now
        obj.ready_at = obj.url = obj.error_message = None
        obj.provider_resource = obj.discovery_json = None

    db.add.side_effect = _stamp

    store = MagicMock()
    enqueue = MagicMock()
    monkeypatch.setattr(storage_mod, "get_artifact_store", lambda **_: store)
    monkeypatch.setattr(redis_mod, "get_redis_client_or_warn", lambda: None)
    monkeypatch.setattr("runtm_api.services.queue.enqueue_deployment", enqueue)
    provider = MagicMock()
    provider.check_deploy.return_value = policy_mod.PolicyCheckResult(allowed=True)
    monkeypatch.setattr(policy_mod, "get_policy_provider", lambda: provider)

    app = FastAPI()
    app.include_router(deployments_router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_auth_context] = lambda: AuthContext(
        token="t", tenant_id="tenant-rain", principal_id="user-jack", api_key_id="key-1", scopes={"deploy"}
    )
    app.dependency_overrides[get_settings] = lambda: MagicMock()
    client = TestClient(app, raise_server_exceptions=False)

    def post(latest, **params):
        q.with_for_update.return_value.first.return_value = latest
        return client.post(
            "/v0/deployments",
            params=params,
            files={"manifest": ("runtm.yaml", MANIFEST), "artifact": ("artifact.zip", _zip())},
        )

    return post, db, store, enqueue


@pytest.mark.parametrize("state", [DeploymentState.QUEUED, DeploymentState.BUILDING, DeploymentState.DEPLOYING])
def test_route_409_while_previous_build_in_flight(harness, state):
    post, db, store, enqueue = harness
    r = post(_latest(state))
    assert r.status_code == 409, r.text
    body = r.json()["detail"]
    assert body["code"] == "deployment_in_progress"
    assert body["deployment_id"] == "dep_9c6b8b665d32"
    assert "recovery_hint" in body
    store.put.assert_not_called()
    enqueue.assert_not_called()
    db.add.assert_not_called()


@pytest.mark.parametrize("state", [DeploymentState.READY, DeploymentState.FAILED])
def test_route_terminal_latest_redeploys(harness, state):
    post, db, store, enqueue = harness
    r = post(_latest(state))
    assert r.status_code == 201, r.text
    created = db.add.call_args.args[0]
    assert created.previous_deployment_id == "dep_9c6b8b665d32"
    assert created.version == 30
    store.put.assert_called_once()
    enqueue.assert_called_once()


def test_route_new_flag_bypasses_the_guard(harness):
    post, db, store, enqueue = harness
    r = post(_latest(DeploymentState.BUILDING), new="true")
    assert r.status_code == 201, r.text
    store.put.assert_called_once()
    enqueue.assert_called_once()
