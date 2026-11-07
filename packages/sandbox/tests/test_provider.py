"""Tests for LocalSandboxProvider."""

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
    """Create a LocalSandboxProvider with temporary directory."""
    from runtm_sandbox.providers.local import LocalSandboxProvider

    provider = LocalSandboxProvider(sandboxes_dir=temp_sandboxes_dir)
    return provider


class TestLocalSandboxProviderCreate:
    """Tests for creating sandboxes."""

    def test_create_returns_sandbox(self, provider) -> None:
        """Should return a Sandbox object."""
        config = SandboxConfig()
        sandbox = provider.create("sbx_test001", config)

        assert isinstance(sandbox, Sandbox)
        assert sandbox.id == "sbx_test001"

    def test_create_sets_running_state(self, provider) -> None:
        """Should set initial state to RUNNING."""
        config = SandboxConfig()
        sandbox = provider.create("sbx_test002", config)

        assert sandbox.state == SandboxState.RUNNING

    def test_create_creates_workspace_directory(self, provider, temp_sandboxes_dir: Path) -> None:
        """Should create the workspace directory."""
        config = SandboxConfig()
        sandbox = provider.create("sbx_test003", config)

        workspace = Path(sandbox.workspace_path)
        assert workspace.exists()
        assert workspace.is_dir()

    def test_create_stores_sandbox_state(self, provider) -> None:
        """Should persist sandbox to state store."""
        config = SandboxConfig()
        provider.create("sbx_test004", config)

        # Should be able to load it back
        loaded = provider.state_store.load("sbx_test004")
        assert loaded is not None
        assert loaded.id == "sbx_test004"

    def test_create_with_custom_config(self, provider) -> None:
        """Should preserve custom configuration."""
        config = SandboxConfig(
            agent=AgentType.CODEX,
            template="web-app",
        )
        sandbox = provider.create("sbx_test005", config)

        assert sandbox.config.agent == AgentType.CODEX
        assert sandbox.config.template == "web-app"

    def test_create_session_id_matches_sandbox_id(self, provider) -> None:
        """For MVP, session_id should equal sandbox_id (1:1)."""
        config = SandboxConfig()
        sandbox = provider.create("sbx_test006", config)

        assert sandbox.session_id == sandbox.id


class TestLocalSandboxProviderLifecycle:
    """Tests for sandbox lifecycle management."""

    def test_stop_changes_state_to_stopped(self, provider) -> None:
        """Should change sandbox state to STOPPED."""
        config = SandboxConfig()
        provider.create("sbx_lifecycle1", config)

        provider.stop("sbx_lifecycle1")

        loaded = provider.state_store.load("sbx_lifecycle1")
        assert loaded is not None
        assert loaded.state == SandboxState.STOPPED

    def test_stop_nonexistent_sandbox_does_not_raise(self, provider) -> None:
        """Should handle stopping non-existent sandbox gracefully."""
        # Should not raise
        provider.stop("sbx_nonexistent")

    def test_destroy_removes_workspace(self, provider, temp_sandboxes_dir: Path) -> None:
        """Should delete the workspace directory."""
        config = SandboxConfig()
        sandbox = provider.create("sbx_destroy1", config)
        workspace = Path(sandbox.workspace_path)
        assert workspace.exists()

        provider.destroy("sbx_destroy1")

        assert not workspace.exists()

    def test_destroy_removes_state(self, provider) -> None:
        """Should remove sandbox from state store."""
        config = SandboxConfig()
        provider.create("sbx_destroy2", config)

        provider.destroy("sbx_destroy2")

        loaded = provider.state_store.load("sbx_destroy2")
        assert loaded is None

    def test_destroy_nonexistent_sandbox_does_not_raise(self, provider) -> None:
        """Should handle destroying non-existent sandbox gracefully."""
        # Should not raise
        provider.destroy("sbx_nonexistent")


class TestLocalSandboxProviderList:
    """Tests for listing sandboxes."""

    def test_list_sandboxes_returns_all(self, provider) -> None:
        """Should return all created sandboxes."""
        provider.create("sbx_list1", SandboxConfig())
        provider.create("sbx_list2", SandboxConfig())
        provider.create("sbx_list3", SandboxConfig())

        sandboxes = provider.list_sandboxes()

        assert len(sandboxes) == 3
        ids = {s.id for s in sandboxes}
        assert ids == {"sbx_list1", "sbx_list2", "sbx_list3"}

    def test_list_sandboxes_returns_empty_when_none(self, provider) -> None:
        """Should return empty list when no sandboxes exist."""
        sandboxes = provider.list_sandboxes()
        assert sandboxes == []

    def test_list_excludes_destroyed_sandboxes(self, provider) -> None:
        """Should not include destroyed sandboxes."""
        provider.create("sbx_keep", SandboxConfig())
        provider.create("sbx_destroy", SandboxConfig())
        provider.destroy("sbx_destroy")

        sandboxes = provider.list_sandboxes()

        assert len(sandboxes) == 1
        assert sandboxes[0].id == "sbx_keep"


class TestLocalSandboxProviderGetState:
    """Tests for getting sandbox state."""

    def test_get_state_returns_correct_state(self, provider) -> None:
        """Should return the current state of sandbox."""
        provider.create("sbx_state1", SandboxConfig())

        state = provider.get_state("sbx_state1")

        assert state == SandboxState.RUNNING

    def test_get_state_returns_destroyed_for_nonexistent(self, provider) -> None:
        """Should return DESTROYED for non-existent sandbox."""
        state = provider.get_state("sbx_nonexistent")

        assert state == SandboxState.DESTROYED

    def test_get_state_reflects_stop(self, provider) -> None:
        """Should return STOPPED after stopping sandbox."""
        provider.create("sbx_state2", SandboxConfig())
        provider.stop("sbx_state2")

        state = provider.get_state("sbx_state2")

        assert state == SandboxState.STOPPED


class TestLocalSandboxProviderAttach:
    """Tests for attaching to sandboxes."""

    def test_attach_raises_for_nonexistent_sandbox(self, provider) -> None:
        """Should raise error when attaching to non-existent sandbox."""
        with pytest.raises(ValueError, match="not found"):
            provider.attach("sbx_nonexistent")

    def test_attach_calls_srt_with_config(self, provider) -> None:
        """Should call srt binary with correct config."""
        provider.create("sbx_attach1", SandboxConfig())

        mock_run = MagicMock(return_value=MagicMock(returncode=0))
        with patch.object(provider, "_check_srt_available", return_value=True):
            with patch("subprocess.run", mock_run):
                exit_code = provider.attach("sbx_attach1")

        assert exit_code == 0
        mock_run.assert_called_once()

        # Check that srt was called
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "srt"

    def test_attach_returns_exit_code(self, provider) -> None:
        """Should return the exit code from the subprocess."""
        provider.create("sbx_attach2", SandboxConfig())

        mock_run = MagicMock(return_value=MagicMock(returncode=42))
        with patch("subprocess.run", mock_run):
            exit_code = provider.attach("sbx_attach2")

        assert exit_code == 42


class TestLocalSandboxProviderTemplates:
    """Tests for template scaffolding."""

    def test_create_without_template_creates_empty_workspace(
        self, provider, temp_sandboxes_dir: Path
    ) -> None:
        """Should create empty workspace when no template specified."""
        config = SandboxConfig(template=None)
        sandbox = provider.create("sbx_no_template", config)

        workspace = Path(sandbox.workspace_path)
        # Workspace should exist but be empty (or have minimal files)
        assert workspace.exists()
        # No template files should be present
        files = list(workspace.iterdir())
        assert len(files) == 0

    def test_create_with_template_scaffolds_files(self, provider) -> None:
        """Should scaffold template files when template specified."""
        config = SandboxConfig(template="backend-service")

        # Mock the template scaffolding
        with patch.object(provider, "_scaffold_template") as mock_scaffold:
            provider.create("sbx_with_template", config)
            mock_scaffold.assert_called_once()


class TestLocalSandboxProviderConfigGeneration:
    """Tests for config file generation during attach."""

    def test_attach_creates_config_file(self, provider, temp_sandboxes_dir: Path) -> None:
        """Should create sandbox-config.json during attach."""
        provider.create("sbx_config1", SandboxConfig())

        with patch.object(provider, "_check_srt_available", return_value=True):
            with patch("subprocess.run", return_value=MagicMock(returncode=0)):
                provider.attach("sbx_config1")

        config_file = temp_sandboxes_dir / "sbx_config1" / "sandbox-config.json"
        assert config_file.exists()

    def test_attach_config_contains_correct_paths(self, provider, temp_sandboxes_dir: Path) -> None:
        """Should include correct write paths in generated config."""
        import json

        config = SandboxConfig()
        provider.create("sbx_config2", config)

        with patch.object(provider, "_check_srt_available", return_value=True):
            with patch("subprocess.run", return_value=MagicMock(returncode=0)):
                provider.attach("sbx_config2")

        config_file = temp_sandboxes_dir / "sbx_config2" / "sandbox-config.json"
        srt_config = json.loads(config_file.read_text())

        assert "." in srt_config["filesystem"]["allowWrite"]
