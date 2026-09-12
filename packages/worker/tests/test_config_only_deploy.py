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

from runtm_shared.types import DeploymentState, Limits, ProviderResource
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
        self.run_kwargs: dict[str, Any] = {}
        self.ready_url: str | None = None
        self.error_message: str | None = None
        self.provider: MagicMock | None = None
        self.log: MagicMock = MagicMock()
        self.ok: bool = False


def _run_config_only(flyctl: Any | None = None) -> _Result:
    """Drive ``DeployJob.run`` down the config-only branch.

    ``flyctl`` optionally replaces the fake flyctl runner (a callable taking
    ``(cmd, **kwargs)``); the default returns a successful ``CompletedProcess``.
    """
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
        yield out.log

    def record_state(_deployment: Any, state: Any, **kwargs: Any) -> None:
        if state == DeploymentState.READY:
            out.ready_url = kwargs.get("url")
        if state == DeploymentState.FAILED:
            out.error_message = kwargs.get("error_message")

    def record_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        out.argv = cmd
        out.run_kwargs = kwargs
        if flyctl is not None:
            return flyctl(cmd, **kwargs)
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
        patch("runtm_worker.jobs.deploy.run_with_graceful_timeout", side_effect=record_run),
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


class TestDeployTimeout:
    """The config-only rollout must use the same ceiling and runner as the build path."""

    def test_uses_env_backed_deploy_timeout(
        self, public: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Limits, "DEPLOY_TIMEOUT_SECONDS", 123)
        out = _run_config_only()
        assert out.ok is True
        assert out.run_kwargs["timeout"] == 123
        assert out.argv[:2] == ["flyctl", "deploy"]

    def test_timeout_keeps_partial_output_and_reports_the_real_ceiling(
        self, public: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Limits, "DEPLOY_TIMEOUT_SECONDS", 123)

        def hanging_flyctl(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
            raise subprocess.TimeoutExpired(
                cmd, kwargs["timeout"], output=b"==> Updating machine abc\n", stderr=b""
            )

        out = _run_config_only(flyctl=hanging_flyctl)
        assert out.ok is False
        assert out.error_message is not None
        assert out.error_message.splitlines()[0] == "Deployment timed out after 123 seconds"
        written = [c.args[0] for c in out.log.write.call_args_list]
        assert "==> Updating machine abc" in written
        assert "Deploy timeout expired after 123s" in written

    def test_nonzero_exit_is_a_deploy_failure_not_a_timeout(self, public: None) -> None:
        def failing_flyctl(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="", stderr="Error: image not found"
            )

        out = _run_config_only(flyctl=failing_flyctl)
        assert out.ok is False
        assert out.error_message is not None
        assert out.error_message.splitlines()[0] == "Deploy failed: Error: image not found"
        assert "timed out" not in out.error_message
