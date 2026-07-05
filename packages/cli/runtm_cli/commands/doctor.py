"""Doctor command - diagnose CLI setup and the local development environment.

Runs three groups of checks:

1. CLI          - version, API URL, auth storage/status, API connectivity.
2. Local sandbox - sandbox dependencies (bun, sandbox-runtime, Claude CLI,
                   bubblewrap). Only shown when the sandbox extras are
                   installed (``runtm-dev`` or ``pip install runtm[sandbox]``).
3. Dev environment - monorepo contributor checks (Python version, .env,
                   FLY_API_TOKEN, Docker, local services). Only shown when
                   run from inside the runtm repository.

Exit code is 0 when no check fails, 1 otherwise, so it can gate scripts:

    runtm doctor && runtm deploy
"""

from __future__ import annotations

import importlib.util
import json
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import typer
from rich.console import Console

console = Console()

# Check statuses
OK = "ok"
WARN = "warn"
FAIL = "fail"
INFO = "info"

# api/worker packages require >= 3.11, so local development does too
DEV_MIN_PYTHON = (3, 11)

# Placeholder values from infra/local.env.example that must be replaced
ENV_PLACEHOLDER_VALUES = {
    "your-fly-personal-access-token-here",
    "your-cloudflare-api-token-here",
    "your-zone-id-here",
}

LOCAL_API_URL = "http://localhost:8000"


@dataclass
class CheckResult:
    """Outcome of a single doctor check."""

    section: str
    name: str
    status: str  # ok | warn | fail | info
    message: str
    hint: str | None = None


def _find_repo_root(start: Path) -> Path | None:
    """Walk up from `start` looking for the runtm monorepo root.

    The root is identified by the presence of both `scripts/dev.sh`
    and `infra/docker-compose.yml`.

    Args:
        start: Directory to start searching from.

    Returns:
        The repo root path, or None if not inside the monorepo.
    """
    for candidate in [start, *start.parents]:
        if (candidate / "scripts" / "dev.sh").is_file() and (
            candidate / "infra" / "docker-compose.yml"
        ).is_file():
            return candidate
    return None


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a .env file into a dict (comments and blank lines ignored).

    Args:
        path: Path to the .env file.

    Returns:
        Mapping of KEY -> value with surrounding quotes stripped.
    """
    values: dict[str, str] = {}
    try:
        content = path.read_text()
    except OSError:
        return values

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key:
            values[key] = value
    return values


def _sandbox_extras_installed() -> bool:
    """Check if the sandbox extras (runtm-sandbox + runtm-agents) are installed."""
    return (
        importlib.util.find_spec("runtm_sandbox") is not None
        and importlib.util.find_spec("runtm_agents") is not None
    )


def _docker_daemon_running() -> bool:
    """Check if the Docker daemon responds (docker CLI must be present)."""
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _check_cli() -> list[CheckResult]:
    """Core CLI checks: API URL, auth storage, auth status, connectivity."""
    import httpx

    from runtm_cli.auth import (
        check_credentials_permissions,
        get_keyring_key,
        get_token,
        get_token_source,
    )
    from runtm_cli.config import get_api_url

    section = "cli"
    results: list[CheckResult] = []

    api_url = get_api_url()
    results.append(CheckResult(section, "API URL", INFO, api_url))

    # Auth storage source
    source = get_token_source()
    if source == "env":
        results.append(CheckResult(section, "Auth storage", OK, "env (RUNTM_API_KEY)"))
    elif source == "keychain":
        results.append(CheckResult(section, "Auth storage", OK, f"keychain ({get_keyring_key()})"))
    elif source == "file":
        results.append(CheckResult(section, "Auth storage", OK, "file (~/.runtm/credentials)"))
    else:
        results.append(
            CheckResult(
                section,
                "Auth storage",
                WARN,
                "no credentials configured",
                hint="Run `runtm login` (or set RUNTM_API_KEY for self-hosted setups)",
            )
        )

    # Auth status - validate token via /v1/me if we have one
    token = get_token()
    if token:
        try:
            response = httpx.get(
                f"{api_url}/v1/me",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5.0,
            )
            if response.status_code == 200:
                email = response.json().get("email", "unknown")
                results.append(CheckResult(section, "Auth status", OK, f"authenticated as {email}"))
            elif response.status_code == 401:
                results.append(
                    CheckResult(
                        section,
                        "Auth status",
                        FAIL,
                        "token invalid or expired",
                        hint="Run `runtm login` to authenticate again",
                    )
                )
            elif response.status_code == 404:
                # /v1/me not available (older self-hosted API) - token presence is all we know
                results.append(CheckResult(section, "Auth status", OK, "token configured"))
            else:
                results.append(
                    CheckResult(
                        section,
                        "Auth status",
                        WARN,
                        f"could not verify (HTTP {response.status_code})",
                    )
                )
        except httpx.TimeoutException:
            results.append(CheckResult(section, "Auth status", WARN, "verification timed out"))
        except Exception as e:
            results.append(CheckResult(section, "Auth status", WARN, f"could not verify: {e}"))
    else:
        results.append(
            CheckResult(
                section,
                "Auth status",
                FAIL,
                "not authenticated",
                hint="Run `runtm login` to authenticate",
            )
        )

    # Credentials file permissions (only relevant for file storage)
    if source == "file":
        perm_ok, perm_msg = check_credentials_permissions()
        results.append(CheckResult(section, "Credentials", OK if perm_ok else WARN, perm_msg))

    # Connectivity check (unauthenticated ping)
    try:
        start = time.time()
        response = httpx.get(f"{api_url}/health", timeout=5.0)
        latency_ms = int((time.time() - start) * 1000)
        if response.status_code == 200:
            results.append(
                CheckResult(section, "Connectivity", OK, f"API reachable ({latency_ms}ms)")
            )
        else:
            results.append(
                CheckResult(
                    section, "Connectivity", WARN, f"API returned HTTP {response.status_code}"
                )
            )
    except httpx.TimeoutException:
        results.append(
            CheckResult(
                section,
                "Connectivity",
                FAIL,
                "API request timed out",
                hint="Check your network, or `runtm config get api_url`",
            )
        )
    except Exception:
        hint = (
            "Run `./scripts/dev.sh up` to start local services"
            if LOCAL_API_URL in api_url
            else "Check your network, or `runtm config get api_url`"
        )
        results.append(
            CheckResult(section, "Connectivity", FAIL, "could not connect to API", hint=hint)
        )

    return results


def _check_sandbox() -> list[CheckResult]:
    """Local sandbox dependency checks (bun, srt, claude, bwrap)."""
    from runtm_sandbox.deps import check_bun, check_bwrap, check_claude, check_srt

    section = "sandbox"
    results: list[CheckResult] = []
    setup_hint = "Run `./scripts/dev.sh setup` (auto-installed on first `runtm start` too)"

    if check_bun():
        runtime = "bun" if shutil.which("bun") else "node"
        results.append(CheckResult(section, "JS runtime", OK, f"{runtime} found"))
    else:
        results.append(
            CheckResult(section, "JS runtime", WARN, "bun/node not found", hint=setup_hint)
        )

    if check_srt():
        results.append(CheckResult(section, "sandbox-runtime", OK, "srt found"))
    else:
        results.append(
            CheckResult(section, "sandbox-runtime", WARN, "srt not found", hint=setup_hint)
        )

    if check_claude():
        results.append(CheckResult(section, "Claude Code CLI", OK, "claude found"))
    else:
        results.append(
            CheckResult(section, "Claude Code CLI", WARN, "claude not found", hint=setup_hint)
        )

    if platform.system() == "Linux":
        if check_bwrap():
            results.append(CheckResult(section, "bubblewrap", OK, "bwrap found"))
        else:
            results.append(
                CheckResult(
                    section,
                    "bubblewrap",
                    WARN,
                    "bwrap not found (required for sandbox isolation on Linux)",
                    hint="Install with your package manager, e.g. `sudo apt install bubblewrap`",
                )
            )

    return results


def _check_dev_env(repo_root: Path) -> list[CheckResult]:
    """Monorepo contributor checks: Python, .env, Docker, local services."""
    import httpx

    section = "dev"
    results: list[CheckResult] = []

    # Python version (api/worker packages require >= 3.11)
    py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    min_version = ".".join(str(p) for p in DEV_MIN_PYTHON)
    if sys.version_info >= DEV_MIN_PYTHON:
        results.append(CheckResult(section, "Python", OK, py_version))
    else:
        results.append(
            CheckResult(
                section,
                "Python",
                FAIL,
                f"{py_version} (>= {min_version} required for api/worker packages)",
                hint=f"Recreate the venv with Python {min_version}+ and re-run `./scripts/dev.sh setup`",
            )
        )

    # .env file + FLY_API_TOKEN
    env_file = repo_root / ".env"
    if env_file.is_file():
        results.append(CheckResult(section, ".env", OK, "found"))
        env_values = _parse_env_file(env_file)
        fly_token = env_values.get("FLY_API_TOKEN", "")
        if not fly_token or fly_token in ENV_PLACEHOLDER_VALUES:
            results.append(
                CheckResult(
                    section,
                    "FLY_API_TOKEN",
                    WARN,
                    "not set (deploys will fail)",
                    hint="Run `fly auth token` and set FLY_API_TOKEN in .env",
                )
            )
        else:
            results.append(CheckResult(section, "FLY_API_TOKEN", OK, "set"))
    else:
        results.append(
            CheckResult(
                section,
                ".env",
                WARN,
                "not found (required for local services)",
                hint="Run `cp infra/local.env.example .env` and add your FLY_API_TOKEN",
            )
        )

    # Docker CLI + daemon
    if shutil.which("docker"):
        if _docker_daemon_running():
            results.append(CheckResult(section, "Docker", OK, "daemon running"))
        else:
            results.append(
                CheckResult(
                    section,
                    "Docker",
                    WARN,
                    "CLI found but daemon not responding",
                    hint="Start Docker Desktop (or the docker service) to run local services",
                )
            )
    else:
        results.append(
            CheckResult(
                section,
                "Docker",
                WARN,
                "not found (required for local services)",
                hint="Install Docker: https://docs.docker.com/get-docker/",
            )
        )

    # Local API health (only meaningful if services are expected to run)
    try:
        response = httpx.get(f"{LOCAL_API_URL}/health", timeout=3.0)
        if response.status_code == 200:
            results.append(
                CheckResult(section, "Local services", OK, f"API healthy at {LOCAL_API_URL}")
            )
        else:
            results.append(
                CheckResult(
                    section,
                    "Local services",
                    WARN,
                    f"API returned HTTP {response.status_code}",
                    hint="Check `./scripts/dev.sh logs api`",
                )
            )
    except Exception:
        results.append(
            CheckResult(
                section,
                "Local services",
                WARN,
                f"API not reachable at {LOCAL_API_URL}",
                hint="Run `./scripts/dev.sh up` to start API + worker + DB + Redis",
            )
        )

    return results


_SECTION_TITLES = {
    "cli": "CLI",
    "sandbox": "Local sandbox",
    "dev": "Dev environment",
}

_STATUS_ICONS = {
    OK: "[green]✓[/green]",
    WARN: "[yellow]⚠[/yellow]",
    FAIL: "[red]✗[/red]",
    INFO: "[dim]·[/dim]",
}


def _render_human(version: str, results: list[CheckResult]) -> None:
    """Render check results grouped by section."""
    console.print()
    console.print(f"[bold]runtm[/bold] v{version}")

    for section_key, title in _SECTION_TITLES.items():
        section_results = [r for r in results if r.section == section_key]
        if not section_results:
            continue
        console.print()
        console.print(f"[bold]{title}[/bold]")
        width = max(len(r.name) for r in section_results)
        for r in section_results:
            icon = _STATUS_ICONS.get(r.status, " ")
            console.print(f"  {icon} {r.name.ljust(width)}  {r.message}")
            if r.hint and r.status in (WARN, FAIL):
                console.print(f"    [dim]↳ {r.hint}[/dim]")

    failures = [r for r in results if r.status == FAIL]
    warnings = [r for r in results if r.status == WARN]
    console.print()
    if failures:
        console.print(f"[red]✗ {len(failures)} problem(s) found[/red]")
    elif warnings:
        console.print(f"[yellow]⚠ Setup OK with {len(warnings)} warning(s)[/yellow]")
    else:
        console.print("[green]✓ All checks passed[/green]")
    console.print()


def doctor_command(json_output: bool = False) -> None:
    """Run all applicable doctor checks and report results.

    Args:
        json_output: Emit results as a single JSON object for AI agents.

    Raises:
        typer.Exit: Exit code 1 if any check fails.
    """
    from runtm_cli import __version__

    results = _check_cli()

    if _sandbox_extras_installed():
        results.extend(_check_sandbox())

    repo_root = _find_repo_root(Path.cwd())
    if repo_root is not None:
        results.extend(_check_dev_env(repo_root))

    ok = not any(r.status == FAIL for r in results)

    if json_output:
        payload = {
            "version": __version__,
            "ok": ok,
            "checks": [asdict(r) for r in results],
        }
        # Plain print: JSON must be parseable, not wrapped by rich
        print(json.dumps(payload, indent=2))
    else:
        _render_human(__version__, results)

    if not ok:
        raise typer.Exit(1)
