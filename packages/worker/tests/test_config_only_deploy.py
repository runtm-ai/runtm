"""The config-only redeploy path must honour the private-deployment rules.

This path exists to apply an env-var or tier change without rebuilding, so it
reuses a previous image and returns before the build phase — and therefore
before every private-mode guard the build path picked up. It still runs a real
``flyctl deploy``, which allocates public ingress for an ``[http_service]`` app
that has no IPs, so on its own it is enough to hand back the public addresses
the rollout released.
"""

from __future__ import annotations

import subprocess
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from runtm_shared.types import DeploymentState, ProviderResource
from runtm_worker.jobs.deploy import DeployJob

APP_NAME = "runtm-dep-abc123de"
DEPLOYMENT_ID = "dep_abc123de4567"
PREVIOUS_ID = "dep_previous1234"
IMAGE_LABEL = "dep-previous1234"
PROXY_DOMAIN = "apps.example.com"
PUBLIC_URL = f"https://{APP_NAME}.example.com"

MANIFEST = {
    "name": "hello",
    "template": "docker",
    "port": 8080,
    "tier": "starter",
}


class _Result:
    """What the caller keeps from a config-only run."""

    def __init__(self) -> None:
        self.argv: list[str] = []
        self.ready_url: str | None = None
        self.provider: MagicMock | None = None
        self.ok: bool = False


def _run_config_only() -> _Result:
    """Drive ``DeployJob.run`` down the config-only branch."""
    out = _Result()

    job = DeployJob(
        db=MagicMock(),
        storage=MagicMock(),
        fly_api_token="test-token",
        redeploy_from=PREVIOUS_ID,
        config_only=True,
    )

    deployment = MagicMock()
    deployment.id = "row-1"
    deployment.manifest_json = MANIFEST
    deployment.state = DeploymentState.QUEUED

    previous = ProviderResource(
        app_name=APP_NAME,
        machine_id="m1",
        region="iad",
        image_ref=f"registry.fly.io/{APP_NAME}:{IMAGE_LABEL}",
        # Written before deployments went private: a host the app has since
        # stopped answering on.
        url=PUBLIC_URL,
    )

    @contextmanager
    def fake_log_capture(*args: Any, **kwargs: Any):
        yield MagicMock()

    def record_state(_deployment: Any, state: Any, **kwargs: Any) -> None:
        if state == DeploymentState.READY:
            out.ready_url = kwargs.get("url")

    def record_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        out.argv = cmd
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="ok", stderr="")

    provider = MagicMock()
    provider._get_app.return_value = {"name": APP_NAME}
    out.provider = provider

    with (
        patch.object(job, "_get_deployment", return_value=deployment),
        patch.object(job, "_get_previous_provider_resource", return_value=(previous, IMAGE_LABEL)),
        patch.object(job, "_transition_state", side_effect=record_state),
        patch.object(job, "_save_provider_resource"),
        patch("runtm_worker.jobs.deploy.LogCapture", fake_log_capture),
        patch("runtm_worker.jobs.deploy.FlyProvider", return_value=provider),
        patch("subprocess.run", side_effect=record_run),
    ):
        out.ok = job.run(DEPLOYMENT_ID)

    return out


@pytest.fixture
def private(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNTM_DEPLOYMENT_PROXY_DOMAIN", PROXY_DOMAIN)


@pytest.fixture
def public(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUNTM_DEPLOYMENT_PROXY_DOMAIN", raising=False)


class TestPrivateMode:
    def test_deploy_cannot_reallocate_public_ips(self, private: None) -> None:
        out = _run_config_only()

        assert out.ok is True
        assert "--no-public-ips" in out.argv

    def test_flycast_is_ensured(self, private: None) -> None:
        """The only path that can reach a private app with no Flycast address."""
        out = _run_config_only()

        assert out.provider is not None
        out.provider.ensure_private_ipv6.assert_called_once_with(APP_NAME)

    def test_inherited_public_url_is_replaced(self, private: None) -> None:
        """Carrying the previous URL forward would publish a dead link."""
        out = _run_config_only()

        assert out.ready_url == f"https://dep-abc123de.{PROXY_DOMAIN}"
        assert out.ready_url != PUBLIC_URL


class TestPublicMode:
    def test_nothing_changes(self, public: None) -> None:
        out = _run_config_only()

        assert out.ok is True
        assert "--no-public-ips" not in out.argv
        assert out.ready_url == PUBLIC_URL
        assert out.provider is not None
        out.provider.ensure_private_ipv6.assert_not_called()
