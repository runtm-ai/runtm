"""Tests for CLI session commands."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from runtm_shared.types import (
    AgentType,
    Sandbox,
    SandboxConfig,
    SandboxState,
    Session,
    SessionMode,
    SessionState,
)


@pytest.fixture
def runner():
    """Create a CLI test runner."""
    return CliRunner()


@pytest.fixture
def temp_sandboxes_dir():
    """Create a temporary directory for sandboxes."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_provider(temp_sandboxes_dir):
    """Create a mock sandbox provider."""
    from runtm_sandbox.providers.local import LocalSandboxProvider

    provider = LocalSandboxProvider(sandboxes_dir=temp_sandboxes_dir)
    return provider


class TestSessionStart:
    """Tests for session start command."""

    def test_start_creates_sandbox(self, runner, temp_sandboxes_dir) -> None:
        """Should create a new sandbox."""
        from runtm_cli.main import app

        with (
            patch("runtm_cli.commands.session.LocalSandboxProvider") as MockProvider,
            patch("runtm_cli.commands.session.SandboxStateStore"),
            patch("runtm_cli.commands.session.ActiveSessionTracker"),
        ):
            mock_provider = MagicMock()
            mock_sandbox = Sandbox(
                id="sbx_test123",
                session_id="sbx_test123",
                config=SandboxConfig(),
                state=SandboxState.RUNNING,
                workspace_path=str(temp_sandboxes_dir / "sbx_test123" / "workspace"),
            )
            mock_provider.create.return_value = mock_sandbox
            mock_provider.attach.return_value = 0
            MockProvider.return_value = mock_provider

            with patch("runtm_cli.commands.session.ensure_sandbox_deps", return_value=True):
                result = runner.invoke(
                    app,
                    ["session", "start", "--local", "--autopilot", "--agent", "claude-code"],
                )

            assert result.exit_code == 0
            mock_provider.create.assert_called_once()

    def test_start_fails_when_deps_missing(self, runner) -> None:
        """Should fail when dependencies are not installed."""
        from runtm_cli.main import app

        with patch("runtm_cli.commands.session.ensure_sandbox_deps", return_value=False):
            result = runner.invoke(
                app,
                ["session", "start", "--local", "--autopilot", "--agent", "claude-code"],
            )

        assert result.exit_code == 1
        assert "dependencies" in result.output.lower() or "missing" in result.output.lower()

    def test_start_with_template(self, runner, temp_sandboxes_dir) -> None:
        """Should pass template to provider."""
        from runtm_cli.main import app

        with (
            patch("runtm_cli.commands.session.LocalSandboxProvider") as MockProvider,
            patch("runtm_cli.commands.session.SandboxStateStore"),
            patch("runtm_cli.commands.session.ActiveSessionTracker"),
        ):
            mock_provider = MagicMock()
            mock_sandbox = Sandbox(
                id="sbx_template",
                session_id="sbx_template",
                config=SandboxConfig(template="backend-service"),
                state=SandboxState.RUNNING,
                workspace_path=str(temp_sandboxes_dir / "sbx_template" / "workspace"),
            )
            mock_provider.create.return_value = mock_sandbox
            mock_provider.attach.return_value = 0
            MockProvider.return_value = mock_provider

            with patch("runtm_cli.commands.session.ensure_sandbox_deps", return_value=True):
                result = runner.invoke(
                    app,
                    [
                        "session",
                        "start",
                        "--local",
                        "--autopilot",
                        "--agent",
                        "claude-code",
                        "--template",
                        "backend-service",
                    ],
                )

            assert result.exit_code == 0
            call_args = mock_provider.create.call_args
            config = call_args[0][1]  # Second positional arg is config
            assert config.template == "backend-service"

    def test_start_with_agent(self, runner, temp_sandboxes_dir) -> None:
        """Should pass agent type to provider."""
        from runtm_cli.main import app

        with (
            patch("runtm_cli.commands.session.LocalSandboxProvider") as MockProvider,
            patch("runtm_cli.commands.session.SandboxStateStore"),
            patch("runtm_cli.commands.session.ActiveSessionTracker"),
        ):
            mock_provider = MagicMock()
            mock_sandbox = Sandbox(
                id="sbx_agent",
                session_id="sbx_agent",
                config=SandboxConfig(agent=AgentType.CODEX),
                state=SandboxState.RUNNING,
                workspace_path=str(temp_sandboxes_dir / "sbx_agent" / "workspace"),
            )
            mock_provider.create.return_value = mock_sandbox
            mock_provider.attach.return_value = 0
            MockProvider.return_value = mock_provider

            with patch("runtm_cli.commands.session.ensure_sandbox_deps", return_value=True):
                result = runner.invoke(
                    app,
                    ["session", "start", "--local", "--autopilot", "--agent", "codex"],
                )

            assert result.exit_code == 0
            call_args = mock_provider.create.call_args
            config = call_args[0][1]
            assert config.agent == AgentType.CODEX


class TestSessionList:
    """Tests for session list command."""

    def test_list_shows_sandboxes(self, runner, temp_sandboxes_dir) -> None:
        """Should list all sessions."""
        from runtm_cli.main import app

        with (
            patch("runtm_cli.commands.session.SandboxStateStore") as MockStateStore,
            patch("runtm_cli.commands.session.ActiveSessionTracker") as MockTracker,
        ):
            mock_store = MagicMock()
            mock_store.list_sessions.return_value = [
                Session(
                    id="sbx_001",
                    mode=SessionMode.AUTOPILOT,
                    state=SessionState.RUNNING,
                    sandbox_id="sbx_001",
                    workspace_path="/tmp/ws1",
                ),
                Session(
                    id="sbx_002",
                    mode=SessionMode.INTERACTIVE,
                    state=SessionState.STOPPED,
                    sandbox_id="sbx_002",
                    workspace_path="/tmp/ws2",
                ),
            ]
            MockStateStore.return_value = mock_store
            mock_tracker = MagicMock()
            mock_tracker.get_active.return_value = None
            MockTracker.return_value = mock_tracker

            result = runner.invoke(app, ["session", "list"])

        assert result.exit_code == 0
        assert "sbx_001" in result.output
        assert "sbx_002" in result.output

    def test_list_shows_empty_message(self, runner) -> None:
        """Should show message when no sandboxes exist."""
        from runtm_cli.main import app

        with patch("runtm_cli.commands.session.LocalSandboxProvider") as MockProvider:
            mock_provider = MagicMock()
            mock_provider.list_sandboxes.return_value = []
            MockProvider.return_value = mock_provider

            result = runner.invoke(app, ["session", "list"])

        assert result.exit_code == 0
        assert "no sandbox" in result.output.lower() or "start one" in result.output.lower()


class TestSessionStop:
    """Tests for session stop command."""

    def test_stop_calls_provider_stop(self, runner) -> None:
        """Should call provider stop method."""
        from runtm_cli.main import app

        with patch("runtm_cli.commands.session.LocalSandboxProvider") as MockProvider:
            mock_provider = MagicMock()
            MockProvider.return_value = mock_provider

            result = runner.invoke(app, ["session", "stop", "sbx_test123"])

        assert result.exit_code == 0
        mock_provider.stop.assert_called_once_with("sbx_test123")


class TestSessionDestroy:
    """Tests for session destroy command."""

    def test_destroy_requires_confirmation(self, runner) -> None:
        """Should require confirmation before destroying."""
        from runtm_cli.main import app

        with patch("runtm_cli.commands.session.LocalSandboxProvider") as MockProvider:
            mock_provider = MagicMock()
            MockProvider.return_value = mock_provider

            # Input 'n' to decline confirmation
            runner.invoke(app, ["session", "destroy", "sbx_test123"], input="n\n")

        # Should not destroy when user declines
        mock_provider.destroy.assert_not_called()

    def test_destroy_with_force_skips_confirmation(self, runner) -> None:
        """Should skip confirmation with --force flag."""
        from runtm_cli.main import app

        with patch("runtm_cli.commands.session.LocalSandboxProvider") as MockProvider:
            mock_provider = MagicMock()
            MockProvider.return_value = mock_provider

            result = runner.invoke(app, ["session", "destroy", "sbx_test123", "--force"])

        assert result.exit_code == 0
        mock_provider.destroy.assert_called_once_with("sbx_test123")


class TestSessionAttach:
    """Tests for session attach command."""

    def test_attach_calls_provider_attach(self, runner) -> None:
        """Should call provider attach method."""
        from runtm_cli.main import app

        with patch("runtm_cli.commands.session.LocalSandboxProvider") as MockProvider:
            mock_provider = MagicMock()
            mock_provider.state_store.load.return_value = Sandbox(
                id="sbx_test123",
                session_id="sbx_test123",
                config=SandboxConfig(),
                state=SandboxState.RUNNING,
                workspace_path="/tmp/ws",
            )
            mock_provider.attach.return_value = 0
            MockProvider.return_value = mock_provider

            result = runner.invoke(app, ["session", "attach", "sbx_test123"])

        assert result.exit_code == 0
        mock_provider.attach.assert_called_once_with("sbx_test123")

    def test_attach_nonexistent_fails(self, runner) -> None:
        """Should fail when sandbox doesn't exist."""
        from runtm_cli.main import app

        with patch("runtm_cli.commands.session.LocalSandboxProvider") as MockProvider:
            mock_provider = MagicMock()
            mock_provider.state_store.load.return_value = None
            MockProvider.return_value = mock_provider

            result = runner.invoke(app, ["session", "attach", "sbx_nonexistent"])

        assert result.exit_code == 1
        assert "not found" in result.output.lower()


class TestDeploymentsListMigration:
    """Tests for the runtm list -> runtm deployments list migration."""

    def test_deployments_list_works(self, runner) -> None:
        """runtm deployments list should work."""
        from runtm_cli.main import app

        with patch("runtm_cli.main.list_command") as mock_list:
            result = runner.invoke(app, ["deployments", "list"])

        # Should call the list_command function
        assert result.exit_code == 0
        mock_list.assert_called_once()

    def test_old_list_still_works(self, runner) -> None:
        """runtm list should still work (for backwards compatibility)."""
        from runtm_cli.main import app

        with patch("runtm_cli.main.list_command") as mock_list:
            result = runner.invoke(app, ["list"])

        # Old command should still work
        assert result.exit_code == 0
        mock_list.assert_called_once()
