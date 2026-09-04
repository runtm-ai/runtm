"""Regression tests for the default Fly remote-deployment path."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from runtm_worker.builder.docker import DockerBuilder
from runtm_worker.jobs.deploy import DeployJob


def test_ensure_fly_app_only_creates_the_app() -> None:
    """The pre-deploy step must not provision networking resources."""
    provider = MagicMock()
    provider._get_app.return_value = None
    build_log = MagicMock()

    created = DeployJob._ensure_fly_app(
        MagicMock(),
        provider=provider,
        app_name="test-app",
        build_log=build_log,
    )

    assert created is True
    provider._create_app.assert_called_once_with("test-app")
    assert provider.method_calls == [
        call._get_app("test-app"),
        call._create_app("test-app"),
    ]


def test_remote_builder_deploys_http_service_successfully(tmp_path: Path) -> None:
    """The default path delegates public-service setup to ``flyctl deploy``."""
    context = tmp_path / "context"
    context.mkdir()
    (context / "Dockerfile").write_text("FROM scratch\n")
    builder = DockerBuilder(use_remote_builder=True)

    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout="Deployment complete",
        stderr="",
    )
    with patch("subprocess.run", return_value=completed) as run:
        result = builder.build_remote(
            context_path=context,
            app_name="test-app",
            deployment_id="dep_abc123test",
            fly_api_token="test-token",
        )

    assert result.success is True
    assert result.deployed is True
    assert result.url == "https://test-app.runtm.com"

    fly_toml = (context / "fly.toml").read_text()
    assert "[http_service]" in fly_toml
    assert "internal_port = 3000" in fly_toml
    assert run.call_args.args[0][:4] == ["flyctl", "deploy", "--app", "test-app"]
    assert "--buildkit" in run.call_args.args[0]
