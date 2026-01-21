"""Runtm CLI - sandboxes where coding agents build and deploy."""

from __future__ import annotations

# NOTE: We intentionally do NOT load .env from the monorepo root.
# The CLI runs in user projects and should only use:
# - User's system environment variables
# - User's project .env.local (via secrets commands)
# - ~/.runtm/config.yaml for CLI config (API URL, token)
import logging
import os

import typer
from rich.console import Console


def _configure_logging(verbose: bool = False) -> None:
    """Configure logging level based on verbose flag or RUNTM_DEBUG env."""
    level = logging.DEBUG if verbose or os.environ.get("RUNTM_DEBUG") else logging.WARNING
    logging.basicConfig(level=level, format="%(message)s", force=True)

    # Suppress noisy third-party loggers even in verbose mode
    noisy_loggers = [
        "httpcore",
        "httpx",
        "urllib3",
        "asyncio",
        "runtm_shared.telemetry",  # Telemetry failures shouldn't clutter output
        "runtm_sandbox",  # Sandbox debug logs
        "runtm_agents",  # Agent debug logs
    ]
    for noisy_logger in noisy_loggers:
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    # Configure structlog to use standard library logging
    # This ensures all structlog loggers respect the logging level
    try:
        import structlog

        structlog.configure(
            processors=[
                structlog.stdlib.filter_by_level,
                structlog.stdlib.add_logger_name,
                structlog.stdlib.add_log_level,
                structlog.stdlib.PositionalArgumentsFormatter(),
                structlog.processors.TimeStamper(fmt="iso"),
                structlog.processors.StackInfoRenderer(),
                structlog.processors.format_exc_info,
                structlog.processors.UnicodeDecoder(),
                structlog.dev.ConsoleRenderer() if verbose else structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.stdlib.BoundLogger,
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=False,  # Allow reconfiguration
        )
    except ImportError:
        pass


# Configure logging at import time (default: quiet)
_configure_logging()

from runtm_cli import __version__
from runtm_cli.commands import (
    approve_command,
    deploy_command,
    destroy_command,
    domain_add_command,
    domain_remove_command,
    domain_status_command,
    fix_command,
    init_command,
    list_command,
    logs_command,
    run_command,
    search_command,
    secrets_get_command,
    secrets_list_command,
    secrets_set_command,
    secrets_unset_command,
    session_app,
    status_command,
    validate_command,
)
from runtm_cli.commands.admin import admin_app

console = Console()


def _main_callback(ctx: typer.Context) -> None:
    """Typer callback for telemetry and interactive mode.

    Runs before any command. If no command specified, launches interactive menu.
    """
    # Import here to avoid circular imports and ensure lazy loading
    from runtm_cli.telemetry import get_telemetry

    # Initialize telemetry (this handles first_run, upgrade detection)
    get_telemetry()

    # If no command specified, launch interactive mode
    if ctx.invoked_subcommand is None:
        _interactive_menu()
        raise typer.Exit(0)


def _interactive_menu() -> None:
    """Interactive menu for starting sessions (agent-friendly)."""
    from rich.prompt import Prompt

    console.print()
    console.print("[bold]runtm[/bold] - Sandboxes where coding agents build and deploy")
    console.print()

    # Check for existing sessions
    try:
        from runtm_sandbox.state import SandboxStateStore
        from runtm_shared.types import SessionState

        state_store = SandboxStateStore()
        sessions = state_store.list_sessions()
        running = [s for s in sessions if s.state == SessionState.RUNNING]

        if running:
            console.print(f"[dim]{len(running)} session(s) running[/dim]")
            console.print()
    except Exception:
        running = []
        sessions = []

    # Menu choices
    choices = ["start", "list", "deploy"]
    if running:
        choices.insert(1, "attach")

    choice = Prompt.ask(
        "What would you like to do?",
        choices=choices + ["quit"],
        default="start",
    )

    if choice == "start":
        # Ask mode
        mode = Prompt.ask(
            "Mode?",
            choices=["autopilot", "interactive"],
            default="autopilot",
        )

        template = Prompt.ask(
            "Template?",
            choices=["none", "backend-service", "web-app", "static-site"],
            default="none",
        )

        # Start session
        from runtm_cli.commands.session import start

        start(
            interactive=(mode == "interactive"),
            local=True,
            template=template if template != "none" else None,
            agent="claude-code",
            name=None,
            no_deploy=False,
        )

    elif choice == "attach":
        if len(running) == 1:
            session_id = running[0].id
        else:
            session_id = Prompt.ask(
                "Which session?",
                choices=[s.id for s in running],
            )
        from runtm_cli.commands.session import attach

        attach(sandbox_id=session_id)

    elif choice == "list":
        from runtm_cli.commands.session import list_sessions

        list_sessions()

    elif choice == "deploy":
        deploy_command(path=".")

    elif choice == "quit":
        pass


# Create main app with callback
app = typer.Typer(
    name="runtm",
    help="Sandboxes where coding agents build and deploy.",
    no_args_is_help=False,  # Allow interactive mode when no command
    invoke_without_command=True,
    callback=_main_callback,
)


@app.command("init")
def init(
    template: str | None = typer.Argument(
        None, help="Template type: backend-service, static-site, web-app"
    ),
    path: str = typer.Option(".", "--path", "-p"),
    name: str = typer.Option(None, "--name", "-n"),
) -> None:
    """Initialize a new project from template."""
    from pathlib import Path

    init_command(
        template=template,
        path=Path(path),
        name=name,
    )


@app.command("deploy")
def deploy(
    path: str = typer.Argument(".", help="Path to project"),
    wait: bool = typer.Option(True, "--wait/--no-wait"),
    timeout: int = typer.Option(500, "--timeout", "-t"),
    new: bool = typer.Option(
        False,
        "--new",
        help="DANGEROUS: Create new Fly app instead of redeploying. Loses custom domains and secrets!",
    ),
    tier: str = typer.Option(None, "--tier", help="Machine tier: starter, standard, performance"),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Auto-fix lockfile issues without prompting"
    ),
    config_only: bool = typer.Option(
        False,
        "--config-only",
        help="Skip Docker build - reuse previous image (for env/tier changes only)",
    ),
    skip_validation: bool = typer.Option(
        False, "--skip-validation", help="Skip Python import validation (use with caution)"
    ),
    force_validation: bool = typer.Option(
        False, "--force-validation", help="Force re-validation even if cached"
    ),
    json_output: bool = typer.Option(False, "--json", help="Output as NDJSON for AI agents"),
) -> None:
    """Deploy a project to a live URL.

    Blocks if lockfile is missing or out of sync. Use --yes to auto-fix.

    Use --config-only to skip the Docker build and reuse the previous image.
    This is useful for changing environment variables or machine tier without
    rebuilding. Requires unchanged source code.

    Use --skip-validation to skip Python import validation (faster but riskier).
    Use --force-validation to ignore validation cache and re-run checks.
    Use --json for NDJSON output for AI agents.
    """
    from pathlib import Path

    # SAFETY: Require explicit confirmation for --new flag (skip in JSON mode)
    if new and not json_output:
        console.print()
        console.print("[red bold]⚠ WARNING: --new creates a NEW Fly app![/red bold]")
        console.print()
        console.print("This will:")
        console.print("  • Create a brand new deployment URL")
        console.print("  • NOT transfer custom domains (e.g., app.runtm.com)")
        console.print("  • NOT transfer environment secrets")
        console.print()
        console.print("Use this ONLY if you intentionally want a separate deployment.")
        console.print()
        if not typer.confirm("Are you sure you want to create a NEW deployment?", default=False):
            console.print(
                "[dim]Cancelled. Use 'runtm deploy' without --new to update existing.[/dim]"
            )
            raise typer.Exit(0)

    deploy_command(
        path=Path(path),
        wait=wait,
        timeout=timeout,
        new=new,
        tier=tier,
        yes=yes,
        config_only=config_only,
        skip_validation=skip_validation,
        force_validation=force_validation,
        json_output=json_output,
    )


@app.command("validate")
def validate(
    path: str = typer.Argument(".", help="Path to project"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON for AI agents"),
) -> None:
    """Validate project before deployment."""
    from pathlib import Path

    validate_command(path=Path(path), json_output=json_output)


@app.command("approve")
def approve(
    path: str = typer.Argument(".", help="Path to project"),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Show what would be applied without making changes"
    ),
) -> None:
    """Apply agent-proposed changes from runtm.requests.yaml.

    Merges requested env vars, connections, and egress allowlist into
    runtm.yaml. After approval, the requests file is deleted.

    In v1, this is informational - deploys work without approval.

    Examples:
        runtm approve              # Apply pending requests
        runtm approve --dry-run    # Preview without applying
    """
    from pathlib import Path

    approve_command(path=Path(path), dry_run=dry_run)


@app.command("fix")
def fix(
    path: str = typer.Argument(".", help="Path to project"),
) -> None:
    """Fix common project issues (lockfiles, etc.).

    Automatically repairs:
    - Missing or drifted lockfiles

    Examples:
        runtm fix              # Fix current directory
        runtm fix ./my-project # Fix specific project
    """
    from pathlib import Path

    fix_command(path=Path(path))


@app.command("run")
def run(
    path: str = typer.Argument(".", help="Path to project"),
    no_install: bool = typer.Option(False, "--no-install", help="Skip dependency installation"),
    no_autofix: bool = typer.Option(False, "--no-autofix", help="Don't auto-fix lockfile drift"),
) -> None:
    """Run project locally (auto-detects runtime from runtm.yaml).

    Starts the development server with the correct port and command
    based on your template's runtime (python, node, or fullstack).

    Uses Bun if available (3x faster), falls back to npm.

    Automatically fixes lockfile drift unless --no-autofix is passed.

    Examples:
        runtm run              # Run current directory
        runtm run ./my-project # Run specific project
        runtm run --no-install # Skip bun/npm/pip install
        runtm run --no-autofix # Don't auto-fix lockfile issues
    """
    from pathlib import Path

    run_command(path=Path(path), install=not no_install, no_autofix=no_autofix)


@app.command("status")
def status(
    deployment_id: str = typer.Argument(..., help="Deployment ID"),
) -> None:
    """Check status of a deployment."""
    status_command(deployment_id=deployment_id)


@app.command("logs")
def logs(
    deployment_id: str = typer.Argument(..., help="Deployment ID"),
    log_type: str = typer.Option(None, "--type", "-t", help="Log type: build, deploy, runtime"),
    lines: int = typer.Option(20, "--lines", "-n", help="Runtime log lines to include"),
    search: str = typer.Option(
        None, "--search", "-s", help="Filter: term, term1,term2 (OR), or regex"
    ),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON for AI agents"),
    raw: bool = typer.Option(False, "--raw", help="Raw output for piping to grep"),
    follow: bool = typer.Option(
        False, "--follow", "-f", help="Follow logs in real-time (coming soon)"
    ),
) -> None:
    """View deployment logs."""
    logs_command(
        deployment_id=deployment_id,
        log_type=log_type,
        lines=lines,
        search=search,
        json_output=json_output,
        raw=raw,
        follow=follow,
    )


@app.command("destroy")
def destroy(
    deployment_id: str = typer.Argument(..., help="Deployment ID to destroy"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
) -> None:
    """Destroy a deployment and stop all running resources."""
    destroy_command(
        deployment_id=deployment_id,
        force=force,
    )


@app.command("list")
def list_deployments(
    state: str = typer.Option(None, "--state", "-s", help="Filter by state"),
    limit: int = typer.Option(50, "--limit", "-n", help="Maximum results"),
) -> None:
    """List all deployments."""
    list_command(state=state, limit=limit)


@app.command("search")
def search_apps(
    query: str = typer.Argument(..., help="Search query (e.g., 'stripe webhook')"),
    state: str = typer.Option(None, "--state", "-s", help="Filter by state"),
    template: str = typer.Option(None, "--template", "-t", help="Filter by template type"),
    limit: int = typer.Option(20, "--limit", "-n", help="Maximum results"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Search deployments by description, tags, and capabilities.

    Searches across runtm.discovery.yaml metadata to find apps.

    Examples:
        runtm search "stripe webhook"
        runtm search "payment" --template backend-service
        runtm search "dashboard" --state ready --json
    """
    search_command(
        query=query,
        state=state,
        template=template,
        limit=limit,
        json_output=json_output,
    )


# Domain subcommand group
domain_app = typer.Typer(
    name="domain",
    help="Manage custom domains for deployments.",
    no_args_is_help=True,
)
app.add_typer(domain_app, name="domain")


@domain_app.command("add")
def domain_add(
    deployment_id: str = typer.Argument(..., help="Deployment ID"),
    hostname: str = typer.Argument(..., help="Custom domain (e.g., api.example.com)"),
) -> None:
    """Add a custom domain to a deployment.

    Configures SSL certificate and shows required DNS records.

    Example:
        runtm domain add dep_abc123 api.example.com
    """
    domain_add_command(deployment_id=deployment_id, hostname=hostname)


@domain_app.command("status")
def domain_status(
    deployment_id: str = typer.Argument(..., help="Deployment ID"),
    hostname: str = typer.Argument(..., help="Custom domain to check"),
) -> None:
    """Check status of a custom domain.

    Shows certificate status and required DNS configuration.

    Example:
        runtm domain status dep_abc123 api.example.com
    """
    domain_status_command(deployment_id=deployment_id, hostname=hostname)


@domain_app.command("remove")
def domain_remove(
    deployment_id: str = typer.Argument(..., help="Deployment ID"),
    hostname: str = typer.Argument(..., help="Custom domain to remove"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Remove a custom domain from a deployment.

    Example:
        runtm domain remove dep_abc123 api.example.com
    """
    domain_remove_command(deployment_id=deployment_id, hostname=hostname, force=force)


# Secrets subcommand group
secrets_app = typer.Typer(
    name="secrets",
    help="Manage local environment secrets (.env.local).",
    no_args_is_help=True,
)
app.add_typer(secrets_app, name="secrets")


@secrets_app.command("set")
def secrets_set(
    key_value: str = typer.Argument(..., help="KEY=VALUE pair to set"),
    path: str = typer.Option(".", "--path", "-p", help="Path to project"),
) -> None:
    """Set a secret in .env.local.

    Secrets are stored locally and injected to the deployment provider
    at deploy time. Runtm never stores secret values.

    Examples:
        runtm secrets set DATABASE_URL=postgres://...
        runtm secrets set API_KEY=sk-xxx
    """
    from pathlib import Path

    secrets_set_command(key_value=key_value, path=Path(path))


@secrets_app.command("get")
def secrets_get(
    key: str = typer.Argument(..., help="Secret name to get"),
    path: str = typer.Option(".", "--path", "-p", help="Path to project"),
) -> None:
    """Get a secret value from .env.local.

    Prints the value to stdout (useful for scripting).

    Example:
        runtm secrets get DATABASE_URL
    """
    from pathlib import Path

    secrets_get_command(key=key, path=Path(path))


@secrets_app.command("list")
def secrets_list(
    path: str = typer.Option(".", "--path", "-p", help="Path to project"),
    show_values: bool = typer.Option(
        False, "--values", "-v", help="Show values (non-secrets only)"
    ),
) -> None:
    """List all secrets and their status.

    Shows which env vars from env_schema are set, missing, or have defaults.

    Example:
        runtm secrets list
        runtm secrets list --values
    """
    from pathlib import Path

    secrets_list_command(path=Path(path), show_values=show_values)


@secrets_app.command("unset")
def secrets_unset(
    key: str = typer.Argument(..., help="Secret name to remove"),
    path: str = typer.Option(".", "--path", "-p", help="Path to project"),
) -> None:
    """Remove a secret from .env.local.

    Example:
        runtm secrets unset OLD_API_KEY
    """
    from pathlib import Path

    secrets_unset_command(key=key, path=Path(path))


# Config subcommand group
config_app = typer.Typer(
    name="config",
    help="Manage CLI configuration.",
    no_args_is_help=True,
)
app.add_typer(config_app, name="config")


@config_app.command("set")
def config_set(
    key_value: str = typer.Argument(..., help="KEY=VALUE pair to set (e.g., api_url=https://...)"),
) -> None:
    """Set a configuration value.

    Valid keys:
    - api_url: API endpoint (for self-hosting)
    - default_template: Default template for init
    - default_runtime: Default runtime

    Examples:
        runtm config set api_url=https://self-hosted.example.com/api
        runtm config set default_template=web-app
    """
    from runtm_cli.config import set_config_value

    if "=" not in key_value:
        console.print("[red]Error:[/red] Invalid format. Use KEY=VALUE")
        console.print()
        console.print("Example: runtm config set api_url=https://api.example.com")
        raise typer.Exit(1)

    key, value = key_value.split("=", 1)
    key = key.strip()
    value = value.strip()

    try:
        set_config_value(key, value)
        console.print(f"[green]✓[/green] Set {key} = {value}")
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@config_app.command("get")
def config_get(
    key: str = typer.Argument(..., help="Config key to get"),
) -> None:
    """Get a configuration value.

    Example:
        runtm config get api_url
    """
    from runtm_cli.config import get_config_value

    try:
        value = get_config_value(key)
        if value is not None:
            console.print(value)
        else:
            console.print("[dim]Not set[/dim]")
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)


@config_app.command("list")
def config_list() -> None:
    """List all configuration values.

    Example:
        runtm config list
    """
    from runtm_cli.config import DEFAULT_API_URL, get_config

    config = get_config()

    console.print()
    console.print("[bold]Configuration[/bold] (~/.runtm/config.yaml)")
    console.print()

    for key, value in config.items():
        # Mark default values
        is_default = key == "api_url" and value == DEFAULT_API_URL
        suffix = " [dim](default)[/dim]" if is_default else ""
        console.print(f"  {key}: {value}{suffix}")

    console.print()


@config_app.command("reset")
def config_reset(
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Reset configuration to defaults.

    Example:
        runtm config reset
    """
    from runtm_cli.config import reset_config

    if not force and not typer.confirm("Reset all config to defaults?", default=False):
        console.print("[dim]Cancelled.[/dim]")
        raise typer.Exit(0)

    reset_config()
    console.print("[green]✓[/green] Configuration reset to defaults")


@app.command("doctor")
def doctor() -> None:
    """Check CLI setup and diagnose issues.

    Displays version, configuration, authentication status,
    and API connectivity. Useful for troubleshooting.

    Example:
        runtm doctor
    """
    import time

    import httpx

    from runtm_cli import __version__
    from runtm_cli.auth import (
        check_credentials_permissions,
        get_keyring_key,
        get_token,
        get_token_source,
    )
    from runtm_cli.config import get_api_url

    console.print()
    console.print(f"[bold]runtm[/bold] v{__version__}")
    console.print()

    # API URL
    api_url = get_api_url()
    console.print(f"  API URL:      {api_url}")

    # Auth storage source
    source = get_token_source()
    if source == "env":
        console.print("  Auth storage: env (RUNTM_API_KEY)")
    elif source == "keychain":
        key = get_keyring_key()
        console.print(f"  Auth storage: keychain ({key})")
    elif source == "file":
        console.print("  Auth storage: file (~/.runtm/credentials)")
    else:
        console.print("  Auth storage: [yellow]none[/yellow]")

    # Auth status - try to validate token via /v1/me if we have one
    token = get_token()
    if token:
        try:
            start = time.time()
            response = httpx.get(
                f"{api_url}/v1/me",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5.0,
            )
            latency_ms = int((time.time() - start) * 1000)

            if response.status_code == 200:
                data = response.json()
                email = data.get("email", "unknown")
                console.print(f"  Auth status:  [green]✓[/green] Authenticated as {email}")
            elif response.status_code == 401:
                console.print("  Auth status:  [red]✗[/red] Token invalid or expired")
            elif response.status_code == 404:
                # /v1/me endpoint doesn't exist yet, fall back to showing token is configured
                console.print("  Auth status:  [green]✓[/green] Token configured")
            else:
                console.print(
                    f"  Auth status:  [yellow]?[/yellow] Could not verify (HTTP {response.status_code})"
                )
        except httpx.TimeoutException:
            console.print("  Auth status:  [yellow]?[/yellow] Verification timed out")
        except Exception as e:
            console.print(f"  Auth status:  [yellow]?[/yellow] Could not verify: {e}")
    else:
        console.print("  Auth status:  [red]✗[/red] Not authenticated")

    # Credentials file permissions (only if using file storage)
    if source == "file":
        perm_ok, perm_msg = check_credentials_permissions()
        if perm_ok:
            console.print(f"  Credentials:  [green]✓[/green] {perm_msg}")
        else:
            console.print(f"  Credentials:  {perm_msg}")

    # Connectivity check (unauthenticated ping)
    try:
        start = time.time()
        response = httpx.get(f"{api_url}/health", timeout=5.0)
        latency_ms = int((time.time() - start) * 1000)

        if response.status_code == 200:
            console.print(f"  Connectivity: [green]✓[/green] API reachable ({latency_ms}ms)")
        else:
            console.print(
                f"  Connectivity: [yellow]?[/yellow] API returned HTTP {response.status_code}"
            )
    except httpx.TimeoutException:
        console.print("  Connectivity: [red]✗[/red] API request timed out")
    except httpx.ConnectError:
        console.print("  Connectivity: [red]✗[/red] Could not connect to API")
    except Exception as e:
        console.print(f"  Connectivity: [red]✗[/red] {e}")

    console.print()

    # Suggested next step
    if not token:
        console.print("  Get started: [cyan]runtm login[/cyan]")
    else:
        console.print("  Ready to deploy! Run: [cyan]runtm init backend-service[/cyan]")

    console.print()


@app.command("login")
def login(
    token: str | None = typer.Option(
        None,
        "--token",
        "-t",
        help="API token (will prompt if not provided)",
    ),
    device: bool = typer.Option(
        False,
        "--device",
        help="Use browser-based device flow (hosted Runtm only)",
    ),
    no_verify: bool = typer.Option(
        False,
        "--no-verify",
        help="Skip API validation (for self-hosted/offline setup)",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Replace existing token without prompting",
    ),
) -> None:
    """Authenticate with Runtm API.

    Two authentication methods:
    - Token: Paste an API token directly (default)
    - Device flow: Authenticate via browser (hosted Runtm only, not yet implemented)

    Examples:
        runtm login                    # Prompt for token
        runtm login --token runtm_xxx  # Provide token directly
        runtm login --no-verify        # Skip API validation (self-hosted)
        runtm login --device           # Browser-based auth (not yet implemented)
    """
    import os

    import httpx

    from runtm_cli.auth import get_token, set_token
    from runtm_cli.config import get_api_url, get_config
    from runtm_cli.telemetry import emit_login_completed, emit_login_started

    # Check if already logged in
    existing_token = get_token()
    if existing_token and not force:
        # Mask the token for display
        masked = (
            existing_token[:16] + "..." + existing_token[-4:]
            if len(existing_token) > 24
            else existing_token[:8] + "..."
        )
        console.print()
        console.print(
            f"[yellow]⚠[/yellow]  You are already logged in with token: [dim]{masked}[/dim]"
        )
        console.print()
        if not typer.confirm("Replace existing token?", default=False):
            console.print("[dim]Keeping existing token.[/dim]")
            raise typer.Exit(0)

    if device:
        # Device flow: only for hosted Runtm or explicitly configured
        config = get_config()
        api_url = config.get("api_url", "")
        device_auth_url = os.environ.get("RUNTM_DEVICE_AUTH_URL", "")

        # Check if device flow is available
        is_hosted = "runtm.dev" in api_url or "runtm.com" in api_url
        if not is_hosted and not device_auth_url:
            console.print("[red]Error:[/red] Device flow only available for hosted Runtm")
            console.print()
            console.print("For self-hosted instances, use --token instead:")
            console.print("  runtm login --token YOUR_TOKEN")
            console.print()
            console.print("Or set RUNTM_DEVICE_AUTH_URL to enable device flow:")
            console.print("  export RUNTM_DEVICE_AUTH_URL=https://your-auth-server/device")
            raise typer.Exit(1)

        # Device flow authentication (coming soon)
        emit_login_started(auth_method="device")
        console.print("[yellow]Device flow coming soon[/yellow]")
        console.print("Please use --token for now")
        raise typer.Exit(1)
    else:
        # Token-based authentication
        if token is None:
            # Show helpful instructions for getting an API key
            console.print()
            console.print("  ┌─────────────────────────────────────────────────┐")
            console.print("  │  [bold]To authenticate with Runtm:[/bold]                    │")
            console.print("  │                                                 │")
            console.print("  │  1. Go to [cyan]https://app.runtm.com[/cyan]                 │")
            console.print("  │  2. Sign in or create an account                │")
            console.print("  │  3. Navigate to [bold]API Keys[/bold]                        │")
            console.print("  │  4. Create a new API key                        │")
            console.print("  │  5. Paste it below                              │")
            console.print("  └─────────────────────────────────────────────────┘")
            console.print()
            token = typer.prompt("  Enter your API key", hide_input=True)

        emit_login_started(auth_method="token")

        # Validate the token via /v1/me endpoint (unless --no-verify)
        if not no_verify:
            console.print()
            console.print("[dim]Validating API key...[/dim]")

            api_url = get_api_url()
            try:
                response = httpx.get(
                    f"{api_url}/v1/me",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=5.0,
                )

                if response.status_code == 200:
                    data = response.json()
                    email = data.get("email", "unknown")
                    console.print(f"[green]✓[/green] Authenticated as {email}")
                elif response.status_code == 401:
                    console.print("[red]✗[/red] Invalid API key")
                    console.print()
                    console.print(
                        "[dim]Please create a new key at https://app.runtm.com/api-keys[/dim]"
                    )
                    raise typer.Exit(1)
                elif response.status_code == 404:
                    # /v1/me endpoint doesn't exist yet, continue without validation
                    console.print("[dim]Skipping validation (endpoint not available)[/dim]")
                else:
                    console.print(
                        f"[yellow]Warning:[/yellow] Could not verify token (HTTP {response.status_code})"
                    )
                    console.print("[dim]Proceeding anyway...[/dim]")
            except httpx.TimeoutException:
                console.print("[yellow]Warning:[/yellow] Validation timed out")
                console.print("[dim]Proceeding anyway...[/dim]")
            except httpx.ConnectError:
                console.print("[yellow]Warning:[/yellow] Could not connect to API")
                console.print("[dim]Proceeding anyway. Use --no-verify to skip validation.[/dim]")
            except Exception as e:
                console.print(f"[yellow]Warning:[/yellow] Could not verify token: {e}")
                console.print("[dim]Proceeding anyway...[/dim]")

        # Store token via unified auth layer (keychain or file)
        storage = set_token(token)
        emit_login_completed(auth_method="token")
        console.print()
        console.print(f"[green]✓[/green] Token saved to {storage}")
        console.print("[dim]You can now use runtm commands.[/dim]")


@app.command("logout")
def logout(
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Skip confirmation prompt",
    ),
) -> None:
    """Remove saved API credentials.

    Clears the stored API token from keychain or credentials file.
    You will need to run 'runtm login' again to authenticate.

    Examples:
        runtm logout          # Prompt for confirmation
        runtm logout --force  # Skip confirmation
    """
    from runtm_cli.auth import clear_token, get_token, get_token_source

    source = get_token_source()

    if source == "none":
        console.print("[dim]No token found. Already logged out.[/dim]")
        raise typer.Exit(0)

    if source == "env":
        console.print("[yellow]⚠[/yellow]  Token is set via RUNTM_API_KEY environment variable.")
        console.print()
        console.print("To log out, unset the environment variable:")
        console.print("  [cyan]unset RUNTM_API_KEY[/cyan]")
        raise typer.Exit(0)

    existing_token = get_token()
    if existing_token:
        # Mask the token for display
        masked = (
            existing_token[:16] + "..." + existing_token[-4:]
            if len(existing_token) > 24
            else existing_token[:8] + "..."
        )

        if not force:
            console.print()
            console.print(f"Current token: [dim]{masked}[/dim]")
            console.print(f"Storage: {source}")
            console.print()
            if not typer.confirm("Remove this token?", default=False):
                console.print("[dim]Token kept.[/dim]")
                raise typer.Exit(0)

    clear_token()
    console.print()
    console.print("[green]✓[/green] Logged out")
    console.print("[dim]Run 'runtm login' to authenticate again.[/dim]")


# Admin subcommand group (for self-host operators)
app.add_typer(admin_app, name="admin")

# Session subcommand group (sandbox management)
app.add_typer(session_app, name="session")


# ============================================================
# Flattened session aliases (for convenience)
# ============================================================


@app.command("start")
def start_alias(
    interactive: bool | None = typer.Option(
        None,
        "--interactive/--autopilot",
        "-i/-a",
        help="Interactive mode (manual) or autopilot mode (via prompts)",
    ),
    template: str | None = typer.Option(
        None,
        "--template",
        "-t",
        help="Template: backend-service, web-app, static-site",
    ),
    agent: str | None = typer.Option(
        None,
        "--agent",
        help="Agent: claude-code, codex, gemini (default: prompt)",
    ),
    name: str | None = typer.Option(None, "--name", "-n", help="Session name"),
    no_deploy: bool = typer.Option(
        False,
        "--no-deploy",
        help="Disable deploy command in sandbox",
    ),
    local: bool = typer.Option(True, "--local/--cloud", help="Local or cloud sandbox"),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose logging (or set RUNTM_DEBUG=1)",
    ),
) -> None:
    """Start a new sandbox session.

    Two modes available:
    - Autopilot: Use `runtm prompt` to send prompts to the agent
    - Interactive: Drop into shell and control agent manually

    If no mode or agent is specified, you'll be prompted to choose.

    Examples:
        runtm start                    # Interactive selection
        runtm start --interactive      # Interactive mode directly
        runtm start --autopilot        # Autopilot mode directly
        runtm start --template web-app # With template
    """
    if verbose:
        _configure_logging(verbose=True)

    from runtm_cli.commands.session import start

    start(
        interactive=interactive,
        local=local,
        template=template,
        agent=agent,
        name=name,
        no_deploy=no_deploy,
    )


@app.command("prompt")
def prompt_alias(
    prompt_text: str = typer.Argument(..., help="Prompt to send to the agent"),
    session_id: str | None = typer.Option(
        None,
        "--session",
        "-s",
        help="Target session ID (default: active session)",
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output"),
    continue_conversation: bool = typer.Option(
        False,
        "--continue",
        "-c",
        help="Continue previous Claude conversation",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Enable verbose logging (or set RUNTM_DEBUG=1)",
    ),
) -> None:
    """Send a prompt to the agent in a sandbox session.

    Examples:
        runtm prompt "Build a todo API with SQLite"
        runtm prompt -s sbx_abc123 "Add tests"
        runtm prompt --continue "Fix the bug"
    """
    if verbose:
        _configure_logging(verbose=True)

    from runtm_cli.commands.session import prompt

    prompt(
        prompt_text=prompt_text,
        session_id=session_id,
        quiet=quiet,
        continue_conversation=continue_conversation,
    )


@app.command("attach")
def attach_alias(
    sandbox_id: str | None = typer.Argument(
        None, help="Sandbox ID to attach to (default: last sandbox in this terminal)"
    ),
) -> None:
    """Attach to an existing sandbox session.

    If no sandbox ID is provided, attaches to the last sandbox created in this terminal.

    Example:
        runtm attach              # Attach to last sandbox in this terminal
        runtm attach sbx_abc123   # Attach to specific sandbox
    """
    from runtm_cli.commands.session import attach

    attach(sandbox_id=sandbox_id)


# Deployments subcommand group (migrate from runtm list)
deployments_app = typer.Typer(name="deployments", help="Manage deployments.")
app.add_typer(deployments_app, name="deployments")


@deployments_app.command("list")
def deployments_list(
    state: str = typer.Option(None, "--state", "-s", help="Filter by state"),
    limit: int = typer.Option(50, "--limit", "-n", help="Maximum results"),
) -> None:
    """List all deployments."""
    list_command(state=state, limit=limit)


@app.command("version")
def version() -> None:
    """Show CLI version."""
    console.print(f"runtm v{__version__}")


def main() -> None:
    """Main entrypoint."""
    app()


if __name__ == "__main__":
    main()
