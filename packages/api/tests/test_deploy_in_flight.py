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
