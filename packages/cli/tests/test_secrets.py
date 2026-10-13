"""Tests for secrets.py CLI commands."""

import os
import tempfile
from pathlib import Path

import pytest

from runtm_cli.commands.secrets import (
    ensure_cursorignore,
    ensure_gitignore,
    get_env_file_path,
    load_local_env,
    resolve_env_vars,
    secrets_get_command,
    secrets_list_command,
    secrets_set_command,
    secrets_unset_command,
)
from runtm_shared.manifest import EnvVar, EnvVarType, Manifest


class TestGetEnvFilePath:
    """Tests for get_env_file_path function."""

    def test_returns_env_local_path(self) -> None:
        """Should return .env.local path."""
        path = Path("/some/project")
        result = get_env_file_path(path)
        assert result == Path("/some/project/.env.local")


class TestEnsureGitignore:
    """Tests for ensure_gitignore function."""

    def test_creates_gitignore_if_missing(self) -> None:
        """Should create .gitignore with .env.local pattern."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)
            ensure_gitignore(path)

            gitignore = path / ".gitignore"
            assert gitignore.exists()
            content = gitignore.read_text()
            assert ".env.local" in content

    def test_adds_to_existing_gitignore(self) -> None:
        """Should add .env.local to existing .gitignore."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)
            gitignore = path / ".gitignore"
            gitignore.write_text("node_modules/\n")

            ensure_gitignore(path)

            content = gitignore.read_text()
            assert "node_modules/" in content
            assert ".env.local" in content

    def test_skips_if_already_present(self) -> None:
        """Should not duplicate .env.local if already present."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)
            gitignore = path / ".gitignore"
            gitignore.write_text(".env.local\n")

            ensure_gitignore(path)

            content = gitignore.read_text()
            assert content.count(".env.local") == 1


class TestEnsureCursorignore:
    """Tests for ensure_cursorignore function."""

    def test_adds_patterns_to_existing_cursorignore(self) -> None:
        """Should add .env patterns to existing .cursorignore."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)
            cursorignore = path / ".cursorignore"
            cursorignore.write_text("node_modules/\n")

            ensure_cursorignore(path)

            content = cursorignore.read_text()
            assert "node_modules/" in content
            assert ".env" in content
            assert ".env.local" in content

    def test_does_not_create_cursorignore(self) -> None:
        """Should not create .cursorignore if it doesn't exist."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)
            ensure_cursorignore(path)

            cursorignore = path / ".cursorignore"
            assert not cursorignore.exists()


class TestLoadLocalEnv:
    """Tests for load_local_env function."""

    def test_loads_env_local(self) -> None:
        """Should load variables from .env.local."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)
            env_local = path / ".env.local"
            env_local.write_text("DATABASE_URL=postgres://localhost\nAPI_KEY=secret123\n")

            result = load_local_env(path)

            assert result["DATABASE_URL"] == "postgres://localhost"
            assert result["API_KEY"] == "secret123"

    def test_loads_env_file(self) -> None:
        """Should load variables from .env."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)
            env_file = path / ".env"
            env_file.write_text("DATABASE_URL=postgres://localhost\n")

            result = load_local_env(path)

            assert result["DATABASE_URL"] == "postgres://localhost"

    def test_env_local_overrides_env(self) -> None:
        """Should prefer .env.local over .env."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)
            (path / ".env").write_text("DATABASE_URL=from_env\n")
            (path / ".env.local").write_text("DATABASE_URL=from_env_local\n")

            result = load_local_env(path)

            assert result["DATABASE_URL"] == "from_env_local"

    def test_returns_empty_if_no_files(self) -> None:
        """Should return empty dict if no env files exist."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)
            result = load_local_env(path)
            assert result == {}


class TestResolveEnvVars:
    """Tests for resolve_env_vars function."""

    def test_resolves_from_env_file(self) -> None:
        """Should resolve env vars from .env.local file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)
            (path / ".env.local").write_text("DATABASE_URL=postgres://localhost\n")

            manifest = Manifest(
                name="test-service",
                template="backend-service",
                runtime="python",
                env_schema=[
                    EnvVar(name="DATABASE_URL", type=EnvVarType.STRING, required=True),
                ],
            )

            resolved, missing, warnings = resolve_env_vars(path, manifest)

            assert resolved["DATABASE_URL"] == "postgres://localhost"
            assert missing == []

    def test_resolves_from_system_env(self) -> None:
        """Should resolve env vars from system environment."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)

            # Set system env var
            os.environ["TEST_VAR_XYZ"] = "from_system"

            try:
                manifest = Manifest(
                    name="test-service",
                    template="backend-service",
                    runtime="python",
                    env_schema=[
                        EnvVar(name="TEST_VAR_XYZ", type=EnvVarType.STRING, required=True),
                    ],
                )

                resolved, missing, warnings = resolve_env_vars(path, manifest)

                assert resolved["TEST_VAR_XYZ"] == "from_system"
                assert missing == []
            finally:
                del os.environ["TEST_VAR_XYZ"]

    def test_uses_default_value(self) -> None:
        """Should use default value if env var not set."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)

            manifest = Manifest(
                name="test-service",
                template="backend-service",
                runtime="python",
                env_schema=[
                    EnvVar(
                        name="LOG_LEVEL",
                        type=EnvVarType.STRING,
                        required=False,
                        default="INFO",
                    ),
                ],
            )

            resolved, missing, warnings = resolve_env_vars(path, manifest)

            assert resolved["LOG_LEVEL"] == "INFO"
            assert missing == []

    def test_reports_missing_required(self) -> None:
        """Should report missing required env vars."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)

            manifest = Manifest(
                name="test-service",
                template="backend-service",
                runtime="python",
                env_schema=[
                    EnvVar(name="REQUIRED_VAR", type=EnvVarType.STRING, required=True),
                ],
            )

            resolved, missing, warnings = resolve_env_vars(path, manifest)

            assert "REQUIRED_VAR" in missing
            assert "REQUIRED_VAR" not in resolved

    def test_env_file_overrides_system_env(self) -> None:
        """Should prefer .env.local over system environment."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)
            (path / ".env.local").write_text("TEST_OVERRIDE=from_file\n")

            # Set system env var
            os.environ["TEST_OVERRIDE"] = "from_system"

            try:
                manifest = Manifest(
                    name="test-service",
                    template="backend-service",
                    runtime="python",
                    env_schema=[
                        EnvVar(name="TEST_OVERRIDE", type=EnvVarType.STRING, required=True),
                    ],
                )

                resolved, missing, warnings = resolve_env_vars(path, manifest)

                # File should take precedence
                assert resolved["TEST_OVERRIDE"] == "from_file"
            finally:
                del os.environ["TEST_OVERRIDE"]


class TestSecretsSetCommand:
    """Tests for secrets_set_command function."""

    def test_sets_secret_value(self) -> None:
        """Should set a secret in .env.local."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)

            secrets_set_command("DATABASE_URL=postgres://localhost", path)

            env_file = path / ".env.local"
            assert env_file.exists()
            content = env_file.read_text()
            assert "DATABASE_URL" in content

    def test_creates_env_file_if_missing(self) -> None:
        """Should create .env.local if it doesn't exist."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)

            secrets_set_command("API_KEY=secret123", path)

            env_file = path / ".env.local"
            assert env_file.exists()

    def test_creates_gitignore(self) -> None:
        """Should ensure .env.local is in .gitignore."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)

            secrets_set_command("API_KEY=secret123", path)

            gitignore = path / ".gitignore"
            assert gitignore.exists()
            content = gitignore.read_text()
            assert ".env.local" in content

    def test_rejects_invalid_format(self) -> None:
        """Should reject input without = sign."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)

            import typer

            with pytest.raises(typer.Exit):
                secrets_set_command("INVALID_FORMAT", path)

    def test_rejects_empty_key(self) -> None:
        """Should reject empty key."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)

            import typer

            with pytest.raises(typer.Exit):
                secrets_set_command("=value", path)

    def test_rejects_invalid_key_format(self) -> None:
        """Should reject key with invalid characters."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)

            import typer

            with pytest.raises(typer.Exit):
                secrets_set_command("invalid-key=value", path)

    def test_allows_alphanumeric_key(self) -> None:
        """Should allow alphanumeric keys with underscores."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)

            secrets_set_command("MY_API_KEY_123=value", path)

            env_file = path / ".env.local"
            content = env_file.read_text()
            assert "MY_API_KEY_123" in content


class TestSecretsGetCommand:
    """Tests for secrets_get_command function."""

    def test_gets_value_from_env_local(self, capsys) -> None:
        """Should get value from .env.local."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)
            (path / ".env.local").write_text("MY_SECRET=secret_value\n")

            secrets_get_command("MY_SECRET", path)

            captured = capsys.readouterr()
            assert "secret_value" in captured.out

    def test_gets_value_from_system_env(self, capsys) -> None:
        """Should get value from system environment."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)

            os.environ["TEST_GET_VAR"] = "system_value"

            try:
                secrets_get_command("TEST_GET_VAR", path)

                captured = capsys.readouterr()
                assert "system_value" in captured.out
            finally:
                del os.environ["TEST_GET_VAR"]

    def test_fails_for_missing_key(self) -> None:
        """Should fail for missing key."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)

            import typer

            with pytest.raises(typer.Exit):
                secrets_get_command("NONEXISTENT_KEY", path)

    def test_value_with_brackets_is_not_corrupted(self, capsys) -> None:
        """Values containing [..] must be emitted verbatim (no Rich markup)."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)
            value = "ab[cd]ef-[bold]not-markup[/bold]"
            (path / ".env.local").write_text(f"BRACKETY={value}\n")

            secrets_get_command("BRACKETY", path)

            captured = capsys.readouterr()
            assert captured.out == value + "\n"

    def test_long_value_is_not_wrapped(self, capsys) -> None:
        """Long values must not have newlines injected (would break scripts)."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)
            value = "x" * 200  # well beyond the 80-col default console width
            (path / ".env.local").write_text(f"LONG={value}\n")

            secrets_get_command("LONG", path)

            captured = capsys.readouterr()
            assert captured.out == value + "\n"

    def test_not_found_error_goes_to_stderr(self, capsys) -> None:
        """The 'not found' message must not pollute stdout used for capture."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)

            import typer

            with pytest.raises(typer.Exit):
                secrets_get_command("NONEXISTENT_KEY", path)

            captured = capsys.readouterr()
            assert captured.out == ""
            assert "not found" in captured.err


class TestSecretsUnsetCommand:
    """Tests for secrets_unset_command function."""

    def test_removes_key_from_env_local(self) -> None:
        """Should remove key from .env.local."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)
            env_file = path / ".env.local"
            env_file.write_text("KEY_TO_REMOVE=value\nOTHER_KEY=other\n")

            secrets_unset_command("KEY_TO_REMOVE", path)

            content = env_file.read_text()
            assert "KEY_TO_REMOVE" not in content
            assert "OTHER_KEY" in content

    def test_handles_missing_env_file(self, capsys) -> None:
        """Should handle missing .env.local gracefully."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)

            # Should not raise
            secrets_unset_command("NONEXISTENT", path)

            captured = capsys.readouterr()
            assert "No .env.local" in captured.out

    def test_handles_missing_key(self, capsys) -> None:
        """Should handle missing key gracefully."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)
            env_file = path / ".env.local"
            env_file.write_text("EXISTING_KEY=value\n")

            secrets_unset_command("NONEXISTENT_KEY", path)

            captured = capsys.readouterr()
            assert "not found" in captured.out


class TestSecretsListCommand:
    """Tests for secrets_list_command function."""

    def test_fails_without_manifest(self) -> None:
        """Should fail if no runtm.yaml exists."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)

            import typer

            with pytest.raises(typer.Exit):
                secrets_list_command(path)

    def test_shows_empty_for_no_env_schema(self, capsys) -> None:
        """Should show message for empty env_schema."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)
            (path / "runtm.yaml").write_text("""
name: test-service
template: backend-service
runtime: python
""")

            secrets_list_command(path)

            captured = capsys.readouterr()
            assert "No env_schema" in captured.out

    def test_shows_env_vars_status(self, capsys) -> None:
        """Should show status of env vars."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir)
            (path / "runtm.yaml").write_text("""
name: test-service
template: backend-service
runtime: python
env_schema:
  - name: DATABASE_URL
    type: string
    required: true
    secret: true
  - name: LOG_LEVEL
    type: string
    required: false
    default: INFO
""")
            (path / ".env.local").write_text("DATABASE_URL=postgres://localhost\n")

            secrets_list_command(path)

            captured = capsys.readouterr()
            assert "DATABASE_URL" in captured.out
            assert "LOG_LEVEL" in captured.out
