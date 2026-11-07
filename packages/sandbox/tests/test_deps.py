"""Tests for dependency checking and installation."""

from __future__ import annotations

import shutil
from unittest.mock import MagicMock, patch


class TestDependencyChecks:
    """Tests for checking if dependencies are installed."""

    def test_check_bun_returns_true_when_bun_installed(self) -> None:
        """Should return True when bun is found in PATH."""
        from runtm_sandbox.deps import check_bun

        with patch.object(shutil, "which", return_value="/usr/local/bin/bun"):
            assert check_bun() is True

    def test_check_bun_returns_true_when_node_installed(self) -> None:
        """Should return True when node is found (fallback from bun)."""
        from runtm_sandbox.deps import check_bun

        def which_side_effect(cmd: str) -> str | None:
            if cmd == "bun":
                return None
            if cmd == "node":
                return "/usr/local/bin/node"
            return None

        with patch.object(shutil, "which", side_effect=which_side_effect):
            assert check_bun() is True

    def test_check_bun_returns_false_when_neither_installed(self) -> None:
        """Should return False when neither bun nor node is found."""
        from runtm_sandbox.deps import check_bun

        with patch.object(shutil, "which", return_value=None):
            assert check_bun() is False

    def test_check_srt_returns_true_when_installed(self) -> None:
        """Should return True when srt binary is found."""
        from runtm_sandbox.deps import check_srt

        with patch.object(shutil, "which", return_value="/usr/local/bin/srt"):
            assert check_srt() is True

    def test_check_srt_returns_false_when_not_installed(self) -> None:
        """Should return False when srt is not found."""
        from runtm_sandbox.deps import check_srt

        with patch.object(shutil, "which", return_value=None):
            assert check_srt() is False

    def test_check_claude_returns_true_when_installed(self) -> None:
        """Should return True when claude CLI is found."""
        from runtm_sandbox.deps import check_claude

        with patch.object(shutil, "which", return_value="/usr/local/bin/claude"):
            assert check_claude() is True

    def test_check_claude_returns_false_when_not_installed(self) -> None:
        """Should return False when claude is not found."""
        from runtm_sandbox.deps import check_claude

        with patch.object(shutil, "which", return_value=None):
            assert check_claude() is False

    def test_check_bwrap_returns_true_when_installed(self) -> None:
        """Should return True when bubblewrap is found."""
        from runtm_sandbox.deps import check_bwrap

        with patch.object(shutil, "which", return_value="/usr/bin/bwrap"):
            assert check_bwrap() is True

    def test_check_bwrap_returns_false_when_not_installed(self) -> None:
        """Should return False when bubblewrap is not found."""
        from runtm_sandbox.deps import check_bwrap

        with patch.object(shutil, "which", return_value=None):
            assert check_bwrap() is False


class TestGetMissingDeps:
    """Tests for getting list of missing dependencies."""

    def test_returns_empty_list_when_all_deps_installed(self) -> None:
        """Should return empty list when all dependencies are present."""
        from runtm_sandbox.deps import get_missing_deps

        with patch.object(shutil, "which", return_value="/some/path"):
            with patch("platform.system", return_value="Darwin"):
                missing = get_missing_deps()
                assert missing == []

    def test_returns_bun_when_bun_missing(self) -> None:
        """Should include bun in missing list when not installed."""
        from runtm_sandbox.deps import get_missing_deps

        def which_side_effect(cmd: str) -> str | None:
            if cmd in ("bun", "node"):
                return None
            return f"/usr/bin/{cmd}"

        with patch.object(shutil, "which", side_effect=which_side_effect):
            with patch("platform.system", return_value="Darwin"):
                missing = get_missing_deps()
                assert any("Bun" in name for name, _ in missing)

    def test_includes_bwrap_on_linux_when_missing(self) -> None:
        """Should check for bubblewrap on Linux."""
        from runtm_sandbox.deps import get_missing_deps

        def which_side_effect(cmd: str) -> str | None:
            if cmd == "bwrap":
                return None
            return f"/usr/bin/{cmd}"

        with patch.object(shutil, "which", side_effect=which_side_effect):
            with patch("platform.system", return_value="Linux"):
                missing = get_missing_deps()
                assert any("bubblewrap" in name for name, _ in missing)

    def test_skips_bwrap_on_macos(self) -> None:
        """Should not check for bubblewrap on macOS (uses seatbelt)."""
        from runtm_sandbox.deps import get_missing_deps

        with patch.object(shutil, "which", return_value="/some/path"):
            with patch("platform.system", return_value="Darwin"):
                missing = get_missing_deps()
                assert not any("bubblewrap" in name for name, _ in missing)


class TestInstallFunctions:
    """Tests for dependency installation functions."""

    def test_install_bun_runs_curl_command(self) -> None:
        """Should run the bun install script."""
        from runtm_sandbox.deps import install_bun

        mock_run = MagicMock(return_value=MagicMock(returncode=0))
        with patch("subprocess.run", mock_run):
            result = install_bun()

        assert result is True
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        # Check that curl is used with bun.sh
        assert "curl" in str(call_args) or "bun.sh" in str(call_args)

    def test_install_bun_returns_false_on_failure(self) -> None:
        """Should return False when installation fails."""
        from runtm_sandbox.deps import install_bun

        mock_run = MagicMock(return_value=MagicMock(returncode=1))
        with patch("subprocess.run", mock_run):
            result = install_bun()

        assert result is False

    def test_install_srt_uses_bun_when_available(self) -> None:
        """Should prefer bun over npm for installing sandbox-runtime."""
        from runtm_sandbox.deps import install_srt

        def which_side_effect(cmd: str) -> str | None:
            if cmd == "bun":
                return "/usr/local/bin/bun"
            return None

        mock_run = MagicMock(return_value=MagicMock(returncode=0))
        with patch.object(shutil, "which", side_effect=which_side_effect):
            with patch("subprocess.run", mock_run):
                result = install_srt()

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "bun"

    def test_install_srt_falls_back_to_npm(self) -> None:
        """Should use npm when bun is not available."""
        from runtm_sandbox.deps import install_srt

        def which_side_effect(cmd: str) -> str | None:
            if cmd == "npm":
                return "/usr/local/bin/npm"
            return None

        mock_run = MagicMock(return_value=MagicMock(returncode=0))
        with patch.object(shutil, "which", side_effect=which_side_effect):
            with patch("subprocess.run", mock_run):
                result = install_srt()

        assert result is True
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "npm"

    def test_install_srt_returns_false_when_no_package_manager(self) -> None:
        """Should return False when neither bun nor npm is available."""
        from runtm_sandbox.deps import install_srt

        with patch.object(shutil, "which", return_value=None):
            result = install_srt()

        assert result is False

    def test_install_claude_runs_native_installer(self) -> None:
        """Should use the native Claude Code installer."""
        from runtm_sandbox.deps import install_claude

        mock_run = MagicMock(return_value=MagicMock(returncode=0))
        with patch("subprocess.run", mock_run):
            result = install_claude()

        assert result is True
        call_args = mock_run.call_args
        # Check that the claude.ai installer is used
        assert "claude.ai/install.sh" in str(call_args)


class TestEnsureSandboxDeps:
    """Tests for the main dependency ensuring function."""

    def test_returns_true_when_all_deps_present(self) -> None:
        """Should return True immediately when all deps are installed."""
        from runtm_sandbox.deps import ensure_sandbox_deps

        with patch.object(shutil, "which", return_value="/some/path"):
            with patch("platform.system", return_value="Darwin"):
                result = ensure_sandbox_deps(auto_install=True)

        assert result is True

    def test_returns_false_when_deps_missing_and_not_approved(self) -> None:
        """Should return False when deps missing and auto_install is False."""
        from runtm_sandbox.deps import ensure_sandbox_deps

        with patch.object(shutil, "which", return_value=None):
            with patch("platform.system", return_value="Darwin"):
                # Don't auto-install, skip prompt - it should return False
                result = ensure_sandbox_deps(auto_install=False, _skip_install=True)

        assert result is False
