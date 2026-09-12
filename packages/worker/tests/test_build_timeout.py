"""Build timeout behaviour of the remote-builder path.

Regression for 2026-09-11: a customer's Next.js build hit the ceiling three
times in a row. Each timeout SIGKILLed flyctl, so the remote builder kept
grinding the abandoned build and the customer got an empty log. These tests
pin the two behaviours that fix that: flyctl is terminated gracefully, and
the partial output is surfaced.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from runtm_worker.builder import docker as docker_module
from runtm_worker.builder.docker import DockerBuilder, _run_with_graceful_timeout

FAKE_FLYCTL_GRACEFUL = """#!/bin/sh
# Prints progress, then hangs. On SIGTERM it records the signal and exits,
# like flyctl cancelling a remote build.
echo "==> Building image"
echo "[12/12] RUN next build"
trap 'echo terminated > "$FAKE_FLYCTL_MARKER"; exit 143' TERM
while :; do sleep 0.1; done
"""

FAKE_FLYCTL_STUBBORN = """#!/bin/sh
echo "==> Building image"
trap '' TERM
while :; do sleep 0.1; done
"""


def _install_fake_flyctl(tmp_path: Path, script: str) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    flyctl = bin_dir / "flyctl"
    flyctl.write_text(script)
    flyctl.chmod(flyctl.stat().st_mode | stat.S_IXUSR)
    return bin_dir


def test_graceful_timeout_sends_sigterm_and_keeps_partial_output(tmp_path: Path) -> None:
    bin_dir = _install_fake_flyctl(tmp_path, FAKE_FLYCTL_GRACEFUL)
    marker = tmp_path / "marker"
    env = {**os.environ, "FAKE_FLYCTL_MARKER": str(marker)}

    with pytest.raises(subprocess.TimeoutExpired) as excinfo:
        _run_with_graceful_timeout(
            [str(bin_dir / "flyctl"), "deploy"],
            cwd=str(tmp_path),
            timeout=1,
            env=env,
        )

    assert marker.read_text().strip() == "terminated", "flyctl never received SIGTERM"
    out = excinfo.value.stdout
    out = out.decode() if isinstance(out, bytes) else out
    assert "[12/12] RUN next build" in out


def test_graceful_timeout_falls_back_to_sigkill(tmp_path: Path) -> None:
    bin_dir = _install_fake_flyctl(tmp_path, FAKE_FLYCTL_STUBBORN)

    with (
        patch.object(docker_module, "_GRACEFUL_TERMINATE_SECONDS", 1),
        pytest.raises(subprocess.TimeoutExpired),
    ):
        _run_with_graceful_timeout(
            [str(bin_dir / "flyctl"), "deploy"],
            cwd=str(tmp_path),
            timeout=1,
            env=dict(os.environ),
        )
    # Reaching here means the SIGTERM-ignoring child was killed and reaped.


def test_graceful_runner_matches_subprocess_run_on_success(tmp_path: Path) -> None:
    result = _run_with_graceful_timeout(
        ["sh", "-c", "echo out; echo err 1>&2; exit 3"],
        cwd=str(tmp_path),
        timeout=5,
        env=dict(os.environ),
    )
    assert result.returncode == 3
    assert result.stdout == "out\n"
    assert result.stderr == "err\n"


def test_build_remote_timeout_surfaces_last_output(tmp_path: Path) -> None:
    """The customer-facing error and the stored build log both carry the tail."""
    bin_dir = _install_fake_flyctl(tmp_path, FAKE_FLYCTL_GRACEFUL)
    context = tmp_path / "context"
    context.mkdir()
    (context / "Dockerfile").write_text("FROM scratch\n")
    builder = DockerBuilder(use_remote_builder=True)

    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "FAKE_FLYCTL_MARKER": str(tmp_path / "marker"),
        "RUNTM_BASE_DOMAIN": "runtm.com",
    }
    with patch.dict("os.environ", env):
        result = builder.build_remote(
            context_path=context,
            app_name="test-app",
            deployment_id="dep_timeout_test",
            fly_api_token="test-token",
            timeout_seconds=1,
        )

    assert result.success is False
    assert result.error is not None
    assert result.error.startswith("Build timeout after 1s")
    assert "Last build output before timeout:" in result.error
    assert "[12/12] RUN next build" in result.error
    assert any("[12/12] RUN next build" in line for line in result.logs)
    assert any("Build timeout expired after 1s" in line for line in result.logs)
    assert (tmp_path / "marker").read_text().strip() == "terminated"
