"""Tests for DockerSandboxProvider."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from runtm_shared.types import (
    AgentType,
    Sandbox,
    SandboxConfig,
    SandboxState,
)


@pytest.fixture
def temp_sandboxes_dir() -> Path:
    """Create a temporary directory for sandboxes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def provider(temp_sandboxes_dir: Path):
    """Create a DockerSandboxProvider with temporary directory."""
    from runtm_sandbox.providers.docker import DockerSandboxProvider

    return DockerSandboxProvider(sandboxes_dir=temp_sandboxes_dir)


class TestDockerSandboxProviderImageMapping:
    """Tests for agent-to-image mapping."""

    def test_claude_code_maps_to_correct_image(self, provider) -> None:
        image = provider._image_for_agent(AgentType.CLAUDE_CODE)
        assert image == "runtm-sandbox/claude-code:latest"

    def test_codex_maps_to_correct_image(self, provider) -> None:
        image = provider._image_for_agent(AgentType.CODEX)
        assert image == "runtm-sandbox/codex:latest"

    def test_unsupported_agent_raises(self, provider) -> None:
        with pytest.raises(ValueError, match="No Docker image defined"):
            provider._image_for_agent(AgentType.GEMINI)


class TestDockerSandboxProviderCreate:
    """Tests for creating Docker sandboxes."""

    @patch("subprocess.run")
    def test_create_returns_sandbox(self, mock_run, provider) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="abc123\n", stderr="")
        config = SandboxConfig(agent=AgentType.CLAUDE_CODE)

        with patch.object(provider, "_image_exists", return_value=True):
            sandbox = provider.create("sbx_test001", config)

        assert isinstance(sandbox, Sandbox)
        assert sandbox.id == "sbx_test001"

    @patch("subprocess.run")
    def test_create_sets_running_state(self, mock_run, provider) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="abc123\n", stderr="")
        config = SandboxConfig()

        with patch.object(provider, "_image_exists", return_value=True):
            sandbox = provider.create("sbx_test002", config)

        assert sandbox.state == SandboxState.RUNNING

    @patch("subprocess.run")
    def test_create_creates_workspace_directory(
        self, mock_run, provider, temp_sandboxes_dir
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="abc123\n", stderr="")
        config = SandboxConfig()

        with patch.object(provider, "_image_exists", return_value=True):
            sandbox = provider.create("sbx_test003", config)

        workspace = Path(sandbox.workspace_path)
        assert workspace.exists()
        assert workspace.is_dir()

    @patch("subprocess.run")
    def test_create_stores_state(self, mock_run, provider) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        config = SandboxConfig()

        with patch.object(provider, "_image_exists", return_value=True):
            provider.create("sbx_test004", config)

        loaded = provider.state_store.load("sbx_test004")
        assert loaded is not None
        assert loaded.id == "sbx_test004"

    @patch("subprocess.run")
    def test_create_maps_ports(self, mock_run, provider) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        config = SandboxConfig(port_mappings={3000: 3000, 8080: 8080})

        with patch.object(provider, "_image_exists", return_value=True):
            provider.create("sbx_ports", config)

        call_args = mock_run.call_args[0][0]
        assert "-p" in call_args
        port_flags = []
        for i, arg in enumerate(call_args):
            if arg == "-p":
                port_flags.append(call_args[i + 1])
        assert "3000:3000" in port_flags
        assert "8080:8080" in port_flags

    @patch("subprocess.run")
    def test_create_passes_api_key(self, mock_run, provider) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        config = SandboxConfig(agent=AgentType.CLAUDE_CODE)

        with (
            patch.object(provider, "_image_exists", return_value=True),
            patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-test-123"}),
        ):
            provider.create("sbx_apikey", config)

        call_args = mock_run.call_args[0][0]
        assert "ANTHROPIC_API_KEY=sk-test-123" in " ".join(call_args)

    @patch("subprocess.run")
    def test_create_passes_openai_key_for_codex(self, mock_run, provider) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        config = SandboxConfig(agent=AgentType.CODEX)

        with (
            patch.object(provider, "_image_exists", return_value=True),
            patch.dict("os.environ", {"OPENAI_API_KEY": "sk-openai-test"}),
        ):
            provider.create("sbx_codexkey", config)

        call_args = mock_run.call_args[0][0]
        assert "OPENAI_API_KEY=sk-openai-test" in " ".join(call_args)

    @patch("subprocess.run")
    def test_create_bind_mounts_workspace(self, mock_run, provider, temp_sandboxes_dir) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        config = SandboxConfig()

        with patch.object(provider, "_image_exists", return_value=True):
            provider.create("sbx_mount", config)

        call_args = mock_run.call_args[0][0]
        assert "-v" in call_args
        vol_idx = call_args.index("-v")
        volume_arg = call_args[vol_idx + 1]
        assert "sbx_mount" in volume_arg
        assert "/home/user" in volume_arg

    @patch("subprocess.run")
    def test_create_builds_image_if_missing(self, mock_run, provider) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        config = SandboxConfig()

        with (
            patch.object(provider, "_image_exists", return_value=False),
            patch.object(provider, "_build_image") as mock_build,
        ):
            provider.create("sbx_build", config)
            mock_build.assert_called_once_with(AgentType.CLAUDE_CODE)

    @patch("subprocess.run")
    def test_create_uses_cached_image(self, mock_run, provider) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        config = SandboxConfig()

        with (
            patch.object(provider, "_image_exists", return_value=True),
            patch.object(provider, "_build_image") as mock_build,
        ):
            provider.create("sbx_cached", config)
            mock_build.assert_not_called()


class TestDockerSandboxProviderLifecycle:
    """Tests for sandbox lifecycle management."""

    @patch("subprocess.run")
    def test_stop_stops_container(self, mock_run, provider) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        config = SandboxConfig()

        with patch.object(provider, "_image_exists", return_value=True):
            provider.create("sbx_stop", config)

        mock_run.reset_mock()
        mock_run.return_value = MagicMock(returncode=0)
        provider.stop("sbx_stop")

        stop_call = mock_run.call_args[0][0]
        assert "docker" in stop_call
        assert "stop" in stop_call

        loaded = provider.state_store.load("sbx_stop")
        assert loaded is not None
        assert loaded.state == SandboxState.STOPPED

    @patch("subprocess.run")
    def test_destroy_removes_container_and_workspace(
        self, mock_run, provider, temp_sandboxes_dir
    ) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        config = SandboxConfig()

        with patch.object(provider, "_image_exists", return_value=True):
            sandbox = provider.create("sbx_destroy", config)

        workspace = Path(sandbox.workspace_path)
        assert workspace.exists()

        mock_run.reset_mock()
        mock_run.return_value = MagicMock(returncode=0)
        provider.destroy("sbx_destroy")

        rm_call = mock_run.call_args[0][0]
        assert "docker" in rm_call
        assert "rm" in rm_call
        assert "-f" in rm_call

        assert not workspace.exists()

        loaded = provider.state_store.load("sbx_destroy")
        assert loaded is None


class TestDockerSandboxProviderList:
    """Tests for listing Docker sandboxes."""

    @patch("subprocess.run")
    def test_list_returns_all_sandboxes(self, mock_run, provider) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        config = SandboxConfig()

        with patch.object(provider, "_image_exists", return_value=True):
            provider.create("sbx_list1", config)
            provider.create("sbx_list2", config)

        sandboxes = provider.list_sandboxes()
        assert len(sandboxes) == 2
        ids = {s.id for s in sandboxes}
        assert ids == {"sbx_list1", "sbx_list2"}

    def test_list_empty(self, provider) -> None:
        sandboxes = provider.list_sandboxes()
        assert sandboxes == []


class TestDockerSandboxProviderGetState:
    """Tests for getting sandbox state from Docker."""

    @patch("subprocess.run")
    def test_get_state_running(self, mock_run, provider) -> None:
        create_mock = MagicMock(returncode=0, stdout="", stderr="")
        mock_run.return_value = create_mock

        with patch.object(provider, "_image_exists", return_value=True):
            provider.create("sbx_state_run", SandboxConfig())

        mock_run.return_value = MagicMock(returncode=0, stdout="running\n", stderr="")
        state = provider.get_state("sbx_state_run")
        assert state == SandboxState.RUNNING

    @patch("subprocess.run")
    def test_get_state_exited(self, mock_run, provider) -> None:
        create_mock = MagicMock(returncode=0, stdout="", stderr="")
        mock_run.return_value = create_mock

        with patch.object(provider, "_image_exists", return_value=True):
            provider.create("sbx_state_exit", SandboxConfig())

        mock_run.return_value = MagicMock(returncode=0, stdout="exited\n", stderr="")
        state = provider.get_state("sbx_state_exit")
        assert state == SandboxState.STOPPED

    def test_get_state_destroyed_for_nonexistent(self, provider) -> None:
        state = provider.get_state("sbx_nonexistent")
        assert state == SandboxState.DESTROYED


class TestDockerSandboxProviderAttach:
    """Tests for attaching to Docker sandboxes."""

    @patch("subprocess.run")
    def test_attach_raises_for_nonexistent(self, mock_run, provider) -> None:
        with pytest.raises(ValueError, match="not found"):
            provider.attach("sbx_nonexistent")

    @patch("subprocess.run")
    def test_attach_calls_docker_exec(self, mock_run, provider) -> None:
        create_mock = MagicMock(returncode=0, stdout="", stderr="")
        mock_run.return_value = create_mock

        with patch.object(provider, "_image_exists", return_value=True):
            provider.create("sbx_attach", SandboxConfig())

        mock_run.reset_mock()
        mock_run.return_value = MagicMock(returncode=0)
        exit_code = provider.attach("sbx_attach")

        assert exit_code == 0
        call_args = mock_run.call_args[0][0]
        assert "docker" in call_args
        assert "exec" in call_args
        assert "-it" in call_args

    @patch("subprocess.run")
    def test_attach_returns_exit_code(self, mock_run, provider) -> None:
        create_mock = MagicMock(returncode=0, stdout="", stderr="")
        mock_run.return_value = create_mock

        with patch.object(provider, "_image_exists", return_value=True):
            provider.create("sbx_exit", SandboxConfig())

        mock_run.reset_mock()
        mock_run.return_value = MagicMock(returncode=42)
        exit_code = provider.attach("sbx_exit")

        assert exit_code == 42


class TestDockerSandboxProviderAvailability:
    """Tests for Docker availability check."""

    def test_is_available_when_docker_present(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/docker"):
            from runtm_sandbox.providers.docker import DockerSandboxProvider

            assert DockerSandboxProvider.is_available() is True

    def test_is_not_available_when_docker_missing(self) -> None:
        with patch("shutil.which", return_value=None):
            from runtm_sandbox.providers.docker import DockerSandboxProvider

            assert DockerSandboxProvider.is_available() is False
