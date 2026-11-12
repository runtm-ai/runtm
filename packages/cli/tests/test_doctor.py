"""Tests for the doctor command."""

import json
from collections import namedtuple
from pathlib import Path
from unittest.mock import patch

import pytest
import typer

from runtm_cli.commands.doctor import (
    FAIL,
    OK,
    WARN,
    CheckResult,
    _check_cli,
    _check_dev_env,
    _find_repo_root,
    _parse_env_file,
    doctor_command,
)

_VersionInfo = namedtuple("_VersionInfo", ["major", "minor", "micro"])


def _results_by_name(results: list) -> dict:
    return {r.name: r for r in results}


# ---------------------------------------------------------------------------
# _parse_env_file
# ---------------------------------------------------------------------------


def test_parse_env_file(tmp_path: Path):
    """Comments, blank lines, and quotes should be handled."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# a comment\n"
        "\n"
        "FLY_API_TOKEN=abc123\n"
        'QUOTED="hello world"\n'
        "SINGLE='quoted'\n"
        "not-a-valid-line\n"
        "EMPTY=\n"
    )

    values = _parse_env_file(env_file)

    assert values["FLY_API_TOKEN"] == "abc123"
    assert values["QUOTED"] == "hello world"
    assert values["SINGLE"] == "quoted"
    assert values["EMPTY"] == ""
    assert "not-a-valid-line" not in values


def test_parse_env_file_missing(tmp_path: Path):
    """A missing file should return an empty dict, not raise."""
    assert _parse_env_file(tmp_path / "nonexistent") == {}


# ---------------------------------------------------------------------------
# _find_repo_root
# ---------------------------------------------------------------------------


def _make_repo_root(tmp_path: Path) -> Path:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "dev.sh").write_text("#!/bin/bash\n")
    (tmp_path / "infra").mkdir()
    (tmp_path / "infra" / "docker-compose.yml").write_text("services: {}\n")
    return tmp_path


def test_find_repo_root_from_nested_dir(tmp_path: Path):
    """The root should be found from a nested subdirectory."""
    root = _make_repo_root(tmp_path)
    nested = root / "packages" / "cli"
    nested.mkdir(parents=True)

    assert _find_repo_root(nested) == root


def test_find_repo_root_outside_repo(tmp_path: Path):
    """Directories without the repo markers should return None."""
    assert _find_repo_root(tmp_path) is None


# ---------------------------------------------------------------------------
# _check_dev_env
# ---------------------------------------------------------------------------


def _run_dev_env_checks(repo_root: Path, **overrides):
    """Run _check_dev_env with external calls stubbed out."""
    defaults = {
        "version_info": _VersionInfo(3, 12, 0),
        "docker_path": "/usr/bin/docker",
        "daemon_running": True,
        "api_up": True,
    }
    defaults.update(overrides)

    class FakeResponse:
        status_code = 200

    def fake_httpx_get(*args, **kwargs):
        if not defaults["api_up"]:
            raise ConnectionError("down")
        return FakeResponse()

    with patch("runtm_cli.commands.doctor.sys") as mock_sys:
        with patch("runtm_cli.commands.doctor.shutil.which", return_value=defaults["docker_path"]):
            with patch(
                "runtm_cli.commands.doctor._docker_daemon_running",
                return_value=defaults["daemon_running"],
            ):
                with patch("httpx.get", side_effect=fake_httpx_get):
                    mock_sys.version_info = defaults["version_info"]
                    return _check_dev_env(repo_root)


def test_dev_env_all_healthy(tmp_path: Path):
    """A fully configured dev environment should report all OK."""
    root = _make_repo_root(tmp_path)
    (root / ".env").write_text("FLY_API_TOKEN=fly_real_token\n")

    checks = _results_by_name(_run_dev_env_checks(root))

    assert checks["Python"].status == OK
    assert checks[".env"].status == OK
    assert checks["FLY_API_TOKEN"].status == OK
    assert checks["Docker"].status == OK
    assert checks["Local services"].status == OK


def test_dev_env_old_python_fails(tmp_path: Path):
    """Python below 3.11 should be a hard failure (api/worker need it)."""
    root = _make_repo_root(tmp_path)
    (root / ".env").write_text("FLY_API_TOKEN=fly_real_token\n")

    checks = _results_by_name(_run_dev_env_checks(root, version_info=_VersionInfo(3, 9, 6)))

    assert checks["Python"].status == FAIL
    assert "3.11" in checks["Python"].message


def test_dev_env_missing_env_file_warns(tmp_path: Path):
    """A missing .env should warn and point at the example file."""
    root = _make_repo_root(tmp_path)

    checks = _results_by_name(_run_dev_env_checks(root))

    assert checks[".env"].status == WARN
    assert "local.env.example" in checks[".env"].hint
    # FLY_API_TOKEN check only runs when .env exists
    assert "FLY_API_TOKEN" not in checks


def test_dev_env_placeholder_fly_token_warns(tmp_path: Path):
    """The example file's placeholder token should not count as configured."""
    root = _make_repo_root(tmp_path)
    (root / ".env").write_text("FLY_API_TOKEN=your-fly-personal-access-token-here\n")

    checks = _results_by_name(_run_dev_env_checks(root))

    assert checks["FLY_API_TOKEN"].status == WARN
    assert "fly auth token" in checks["FLY_API_TOKEN"].hint


def test_dev_env_docker_and_services_down(tmp_path: Path):
    """Docker missing and API down should warn with recovery hints."""
    root = _make_repo_root(tmp_path)
    (root / ".env").write_text("FLY_API_TOKEN=fly_real_token\n")

    checks = _results_by_name(_run_dev_env_checks(root, docker_path=None, api_up=False))

    assert checks["Docker"].status == WARN
    assert checks["Local services"].status == WARN
    assert "dev.sh up" in checks["Local services"].hint


# ---------------------------------------------------------------------------
# _check_cli
# ---------------------------------------------------------------------------


def test_check_cli_not_authenticated():
    """Missing credentials should fail the auth check with a login hint."""

    class FakeResponse:
        status_code = 200

    with patch("runtm_cli.auth.get_token", return_value=None):
        with patch("runtm_cli.auth.get_token_source", return_value="none"):
            with patch("runtm_cli.config.get_api_url", return_value="https://api.example.com"):
                with patch("httpx.get", return_value=FakeResponse()):
                    checks = _results_by_name(_check_cli())

    assert checks["Auth storage"].status == WARN
    assert checks["Auth status"].status == FAIL
    assert "runtm login" in checks["Auth status"].hint
    assert checks["Connectivity"].status == OK


def test_check_cli_authenticated():
    """A valid token should report the authenticated email."""

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"email": "dev@example.com"}

    with patch("runtm_cli.auth.get_token", return_value="runtm_token"):
        with patch("runtm_cli.auth.get_token_source", return_value="env"):
            with patch("runtm_cli.config.get_api_url", return_value="https://api.example.com"):
                with patch("httpx.get", return_value=FakeResponse()):
                    checks = _results_by_name(_check_cli())

    assert checks["Auth storage"].status == OK
    assert checks["Auth status"].status == OK
    assert "dev@example.com" in checks["Auth status"].message


# ---------------------------------------------------------------------------
# doctor_command
# ---------------------------------------------------------------------------


def _patched_doctor(results: list, json_output: bool):
    with patch("runtm_cli.commands.doctor._check_cli", return_value=results):
        with patch("runtm_cli.commands.doctor._sandbox_extras_installed", return_value=False):
            with patch("runtm_cli.commands.doctor._find_repo_root", return_value=None):
                doctor_command(json_output=json_output)


def test_doctor_json_output(capsys):
    """--json should emit a parseable payload with all checks."""
    results = [
        CheckResult("cli", "Auth status", OK, "authenticated as dev@example.com"),
        CheckResult("cli", "Connectivity", OK, "API reachable (10ms)"),
    ]

    _patched_doctor(results, json_output=True)

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert len(payload["checks"]) == 2
    assert payload["checks"][0]["name"] == "Auth status"
    assert payload["checks"][0]["status"] == OK


def test_doctor_exits_nonzero_on_failure():
    """Any failing check should produce exit code 1 for scriptability."""
    results = [
        CheckResult("cli", "Auth status", FAIL, "not authenticated", hint="Run `runtm login`")
    ]

    with pytest.raises(typer.Exit) as exc_info:
        _patched_doctor(results, json_output=True)

    assert exc_info.value.exit_code == 1


def test_doctor_warnings_do_not_fail(capsys):
    """Warnings alone should keep ok=true and not raise an exit."""
    results = [
        CheckResult("cli", "Auth status", OK, "token configured"),
        CheckResult("dev", "FLY_API_TOKEN", WARN, "not set"),
    ]

    _patched_doctor(results, json_output=True)

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
