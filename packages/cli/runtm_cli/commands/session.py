"""Session commands for managing sandbox sessions.

Commands:
- runtm session start: Start a new sandbox session (autopilot/interactive)
- runtm session prompt: Send a prompt to an agent in autopilot mode
- runtm session list: List all sandbox sessions
- runtm session attach: Attach to an existing session
- runtm session stop: Stop a session (preserves workspace)
- runtm session destroy: Destroy a session and delete workspace
- runtm session deploy: Deploy from a sandbox to a live URL
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path

import questionary
import typer
from questionary import Style
from rich.console import Console

from runtm_shared.types import AgentType, SandboxConfig

console = Console()
session_app = typer.Typer(name="session", help="Manage sandbox sessions.")

# Custom style for questionary prompts - emerald green theme
PROMPT_STYLE = Style(
    [
        ("qmark", "fg:#10b981 bold"),
        ("question", "bold"),
        ("answer", "fg:#10b981 bold"),
        ("pointer", "fg:#10b981 bold"),
        ("highlighted", "fg:#10b981 bold"),
        ("selected", "fg:#10b981"),
        ("separator", "fg:#71717a"),
        ("instruction", "fg:#71717a"),
    ]
)

# Mode options for interactive selection
MODE_OPTIONS = [
    {
        "name": "autopilot",
        "title": "Autopilot",
        "description": "Send prompts via CLI, agent works autonomously",
    },
    {
        "name": "interactive",
        "title": "Interactive",
        "description": "Drop into shell, control the agent manually",
    },
]

# Agent options for interactive selection
AGENT_OPTIONS = [
    {
        "name": "claude-code",
        "title": "Claude Code",
        "description": "Anthropic's coding agent (recommended)",
    },
    {
        "name": "codex",
        "title": "Codex",
        "description": "OpenAI's Codex CLI agent",
    },
    {
        "name": "gemini",
        "title": "Gemini",
        "description": "Google's Gemini CLI agent",
    },
]


def _prompt_mode_selection() -> str:
    """Prompt user to select session mode interactively.

    Returns:
        Selected mode name ('autopilot' or 'interactive')
    """
    choices = [
        questionary.Choice(
            title=f"[{i + 1}] {m['title']} - {m['description']}",
            value=m["name"],
            shortcut_key=str(i + 1),
        )
        for i, m in enumerate(MODE_OPTIONS)
    ]

    console.print()
    console.print("[dim]Select a mode (arrow keys + Enter, or press 1/2):[/dim]")
    console.print()

    result = questionary.select(
        "How do you want to run the sandbox?",
        choices=choices,
        style=PROMPT_STYLE,
        instruction="",
        use_shortcuts=True,
    ).ask()

    if result is None:
        raise typer.Exit(0)

    return result


def _prompt_agent_selection() -> str:
    """Prompt user to select an agent interactively.

    Returns:
        Selected agent name ('claude-code', 'codex', or 'gemini')
    """
    choices = [
        questionary.Choice(
            title=f"[{i + 1}] {a['title']} - {a['description']}",
            value=a["name"],
            shortcut_key=str(i + 1),
        )
        for i, a in enumerate(AGENT_OPTIONS)
    ]

    console.print()
    console.print("[dim]Select an agent (arrow keys + Enter, or press 1/2/3):[/dim]")
    console.print()

    result = questionary.select(
        "Which coding agent do you want to use?",
        choices=choices,
        style=PROMPT_STYLE,
        instruction="",
        use_shortcuts=True,
    ).ask()

    if result is None:
        raise typer.Exit(0)

    return result


# Sandbox and agents packages are optional - check availability
SANDBOX_AVAILABLE = False
AGENTS_AVAILABLE = False

try:
    from runtm_sandbox.deps import ensure_sandbox_deps
    from runtm_sandbox.providers.local import LocalSandboxProvider
    from runtm_sandbox.state import ActiveSessionTracker, SandboxStateStore

    SANDBOX_AVAILABLE = True
except ImportError:
    pass

try:
    from runtm_agents import run_prompt_in_sandbox

    AGENTS_AVAILABLE = True
except ImportError:
    pass


def _require_sandbox() -> None:
    """Check if sandbox package is available, exit with helpful message if not."""
    if not SANDBOX_AVAILABLE:
        console.print("[red]Sandbox package not installed.[/red]")
        console.print()
        console.print("Install with:")
        console.print("  [cyan]pip install runtm[sandbox][/cyan]")
        console.print()
        console.print("Or for development:")
        console.print("  [cyan]pip install -e packages/sandbox -e packages/agents[/cyan]")
        raise typer.Exit(1)


DOCKER_PROVIDER_AVAILABLE = False
try:
    from runtm_sandbox.providers.docker import DockerSandboxProvider

    DOCKER_PROVIDER_AVAILABLE = True
except ImportError:
    pass


def _get_sandbox_provider(provider_name: str | None = None):
    """Select sandbox provider by name.

    Resolution order:
      1. Explicit --provider flag
      2. RUNTM_SANDBOX_PROVIDER env var
      3. Auto-detect: docker if available, else local
    """
    import os
    import shutil

    name = provider_name or os.environ.get("RUNTM_SANDBOX_PROVIDER")

    if name is None:
        name = "docker" if DOCKER_PROVIDER_AVAILABLE and shutil.which("docker") else "local"

    if name == "docker":
        if not DOCKER_PROVIDER_AVAILABLE:
            console.print("[red]Docker sandbox provider not available.[/red]")
            console.print("Install sandbox package: [cyan]pip install runtm[sandbox][/cyan]")
            raise typer.Exit(1)
        if not shutil.which("docker"):
            console.print("[red]Docker not found.[/red]")
            console.print()
            console.print(
                "Install Docker Desktop: [cyan]https://www.docker.com/products/docker-desktop[/cyan]"
            )
            raise typer.Exit(1)
        return DockerSandboxProvider()  # type: ignore[name-defined]

    if name == "local":
        return LocalSandboxProvider()  # type: ignore[name-defined]

    console.print(f"[red]Unknown sandbox provider: {name}[/red]")
    console.print("Valid providers: docker, local")
    raise typer.Exit(1)


def _require_agents() -> None:
    """Check if agents package is available, exit with helpful message if not."""
    if not AGENTS_AVAILABLE:
        console.print("[red]Agents package not installed.[/red]")
        console.print()
        console.print("Install with:")
        console.print("  [cyan]pip install runtm[sandbox][/cyan]")
        console.print()
        console.print("Or for development:")
        console.print("  [cyan]pip install -e packages/agents[/cyan]")
        raise typer.Exit(1)


def _format_relative_time(dt: datetime) -> str:
    """Format datetime as relative time."""
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    diff = now - dt

    if diff.days > 0:
        return f"{diff.days} day{'s' if diff.days > 1 else ''} ago"
    elif diff.seconds >= 3600:
        hours = diff.seconds // 3600
        return f"{hours} hour{'s' if hours > 1 else ''} ago"
    elif diff.seconds >= 60:
        minutes = diff.seconds // 60
        return f"{minutes} min ago"
    else:
        return "just now"


def _configure_logging(verbose: bool = False) -> None:
    """Configure logging level based on verbose flag or RUNTM_DEBUG env."""
    import logging
    import os

    level = logging.DEBUG if verbose or os.environ.get("RUNTM_DEBUG") else logging.WARNING
    logging.basicConfig(level=level, format="%(message)s", force=True)

    # Suppress noisy third-party loggers even in verbose mode
    noisy_loggers = [
        "httpcore",
        "httpx",
        "urllib3",
        "asyncio",
        "runtm_shared.telemetry",  # Telemetry failures shouldn't clutter output
    ]
    for noisy_logger in noisy_loggers:
        logging.getLogger(noisy_logger).setLevel(logging.WARNING)

    try:
        import structlog

        structlog.configure(
            wrapper_class=structlog.make_filtering_bound_logger(level),
        )
    except ImportError:
        pass


@session_app.command("start")
def start(
    interactive: bool | None = typer.Option(
        None,
        "--interactive/--autopilot",
        "-i/-a",
        help="Interactive mode (manual) or autopilot mode (via prompts)",
    ),
    local: bool = typer.Option(True, "--local/--cloud", help="Local or cloud sandbox"),
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
    provider: str | None = typer.Option(
        None,
        "--provider",
        "-p",
        help="Sandbox provider: docker (recommended), local (srt process sandbox)",
    ),
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

    Examples:
        runtm start                    # Interactive mode selection
        runtm start --interactive      # Interactive mode directly
        runtm start --autopilot        # Autopilot mode directly
        runtm start --template web-app # With template
    """
    if verbose:
        _configure_logging(verbose=True)

    _require_sandbox()

    # Import types that depend on sandbox package
    from runtm_shared.types import (
        Session,
        SessionConstraints,
        SessionMode,
        SessionState,
    )

    # If mode not specified, prompt interactively
    if interactive is None:
        selected_mode = _prompt_mode_selection()
        interactive = selected_mode == "interactive"

    # If agent not specified, prompt interactively
    if agent is None:
        agent = _prompt_agent_selection()

    # 1. Resolve provider and check deps
    sandbox_provider = _get_sandbox_provider(provider)
    use_docker = hasattr(sandbox_provider, "_container_name")

    if not use_docker:
        if not ensure_sandbox_deps(auto_install=False):  # type: ignore[name-defined]
            console.print("[red]Missing sandbox dependencies.[/red]")
            console.print()
            console.print("For development, run:")
            console.print("  [cyan]./scripts/dev.sh setup[/cyan]")
            console.print()
            console.print("Or install manually:")
            console.print("  curl -fsSL https://bun.sh/install | bash")
            console.print("  bun install -g @anthropic-ai/sandbox-runtime")
            console.print("  curl -fsSL https://claude.ai/install.sh | bash")
            console.print()
            console.print("Or use Docker instead:")
            console.print("  [cyan]runtm start --provider docker[/cyan]")
            raise typer.Exit(1)

    # 2. Validate agent type
    try:
        agent_type = AgentType(agent)
    except ValueError:
        console.print(f"[red]Invalid agent: {agent}[/red]")
        console.print("Valid agents: claude-code, codex, gemini, custom")
        raise typer.Exit(1)

    # 3. Create sandbox
    session_id = f"sbx_{uuid.uuid4().hex[:12]}"
    mode = SessionMode.INTERACTIVE if interactive else SessionMode.AUTOPILOT

    config = SandboxConfig(
        agent=agent_type,
        template=template,
    )

    constraints = SessionConstraints(
        allow_deploy=not no_deploy,
    )

    console.print(f"\n[dim]Creating sandbox {session_id}...[/dim]")
    sandbox = sandbox_provider.create(session_id, config)

    # 4. Create session record
    session = Session(
        id=session_id,
        name=name,
        mode=mode,
        state=SessionState.RUNNING,
        agent=agent_type,
        sandbox_id=session_id,
        workspace_path=sandbox.workspace_path,
        constraints=constraints,
    )

    # Save session
    state_store = SandboxStateStore()  # type: ignore[name-defined]
    state_store.save_session(session)

    # 5. Set as active session
    tracker = ActiveSessionTracker()  # type: ignore[name-defined]
    tracker.set_active(session_id)

    # 6. Mode-specific behavior
    if mode == SessionMode.INTERACTIVE:
        # Interactive: drop into shell
        console.print(f"[green]✓[/green] Sandbox ready ({session_id})")
        console.print()

        # Attach to sandbox (drops user into isolated shell)
        sandbox_provider.attach(session_id)

        # Always show exit message
        console.print()
        console.print(f"[dim]Left sandbox {session_id}[/dim]")
        console.print()
        console.print("Session is still running. You can:")
        console.print(f"  [cyan]runtm attach {session_id}[/cyan]  - Reattach to this sandbox")
        console.print('  [cyan]runtm prompt "..."[/cyan]        - Send a prompt (autopilot)')
        console.print(f"  [cyan]runtm session stop {session_id}[/cyan] - Stop the session")
    else:
        # Autopilot: ready for prompts
        console.print(f"[green]✓[/green] Sandbox ready ({session_id})")
        console.print("[green]✓[/green] Claude Code ready (autopilot mode)")
        console.print(f"  Workspace: [dim]{sandbox.workspace_path}[/dim]")
        console.print()
        console.print("Send a prompt to start building:")
        console.print('  [cyan]runtm prompt "Build a todo API with SQLite"[/cyan]')


@session_app.command("prompt")
def prompt(
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

    Requires a session in autopilot mode. Creates the session if none exists.

    Examples:
        runtm session prompt "Build a todo API"
        runtm session prompt -s sbx_abc123 "Add tests"
        runtm session prompt --continue "Fix the bug"
    """
    if verbose:
        _configure_logging(verbose=True)

    _require_sandbox()
    _require_agents()

    from runtm_shared.types import SessionMode, SessionState

    # 1. Resolve session
    tracker = ActiveSessionTracker()  # type: ignore[name-defined]
    state_store = SandboxStateStore()  # type: ignore[name-defined]

    if session_id is None:
        session_id = tracker.get_active()
        if not session_id:
            console.print("[red]No active session. Start one with:[/red]")
            console.print("  runtm start")
            raise typer.Exit(1)

    session = state_store.load_session(session_id)
    if not session:
        console.print(f"[red]Session not found: {session_id}[/red]")
        raise typer.Exit(1)

    # 2. Validate mode
    if session.mode == SessionMode.INTERACTIVE:
        console.print("[yellow]Session is in interactive mode.[/yellow]")
        console.print("Use [cyan]runtm attach[/cyan] to control Claude manually,")
        console.print("or start a new session with [cyan]runtm start[/cyan] (autopilot).")
        raise typer.Exit(1)

    # 3. Validate state
    if session.state != SessionState.RUNNING:
        console.print(f"[red]Session is {session.state.value}.[/red]")
        console.print("Start a new session with [cyan]runtm start[/cyan]")
        raise typer.Exit(1)

    # 4. Get Claude session ID for continuation
    claude_session = session.claude_session_id if continue_conversation else None

    # 5. Load sandbox
    sandbox = state_store.load(session_id)
    if not sandbox:
        console.print(f"[red]Sandbox not found for session: {session_id}[/red]")
        raise typer.Exit(1)

    # 6. Run prompt
    console.print()

    async def run() -> None:
        nonlocal claude_session
        files_modified: list[str] = []
        commands_run: list[str] = []
        last_error: str | None = None
        success = False
        start_time = datetime.now(timezone.utc)

        with console.status("[bold]Claude is working...[/bold]", spinner="dots"):
            async for output in run_prompt_in_sandbox(  # type: ignore[name-defined]
                sandbox=sandbox,
                prompt=prompt_text,
                continue_session=claude_session,
                stream=True,
            ):
                if output.type == "text":
                    if not quiet:
                        console.print(f"  {output.content}")
                elif output.type == "tool_use":
                    if not quiet:
                        console.print(f"  [dim]{output.content}[/dim]")
                    # Track files and commands
                    if output.metadata:
                        tool = output.metadata.get("tool", "")
                        if tool in ("Write", "Edit"):
                            file_path = output.metadata.get("input", {}).get("file_path", "")
                            if file_path:
                                files_modified.append(file_path)
                        elif tool == "Bash":
                            cmd = output.metadata.get("input", {}).get("command", "")
                            if cmd:
                                commands_run.append(cmd)
                elif output.type == "error":
                    last_error = output.content
                    if not quiet:
                        console.print(f"  [red]{output.content}[/red]")
                elif output.type == "result":
                    success = True
                    # Extract session ID from result
                    if output.metadata:
                        claude_session = output.metadata.get("session_id")

        # Calculate duration
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()

        # Update session
        if session.initial_prompt is None:
            session.initial_prompt = prompt_text[:100]  # Truncate for display
        if claude_session:
            session.claude_session_id = claude_session
        session.updated_at = datetime.now(timezone.utc)
        state_store.save_session(session)

        # Print summary
        console.print()
        if success:
            console.print(f"[green]✓[/green] Done ({duration:.1f}s)")
            if files_modified:
                console.print(f"  Files: {len(files_modified)}")
            if commands_run:
                console.print(f"  Commands: {len(commands_run)}")
        else:
            error_msg = (
                last_error or "Unknown error (check if Claude is authenticated: claude /doctor)"
            )
            console.print(f"[red]✗[/red] Failed: {error_msg}")

    asyncio.run(run())


@session_app.command("list")
def list_sessions(
    running_only: bool = typer.Option(
        False,
        "--running",
        "-r",
        help="Only show running sessions",
    ),
) -> None:
    """List all sandbox sessions.

    Shows sessions with their state, agent, and initial prompt.

    Example:
        runtm session list
        runtm session list --running
    """
    _require_sandbox()

    from runtm_shared.types import SessionState

    state_store = SandboxStateStore()  # type: ignore[name-defined]
    tracker = ActiveSessionTracker()  # type: ignore[name-defined]

    sessions = state_store.list_sessions()

    if running_only:
        sessions = [s for s in sessions if s.state == SessionState.RUNNING]

    if not sessions:
        console.print("[dim]No sessions found.[/dim]")
        console.print("Start one with: [cyan]runtm start[/cyan]")
        return

    active_id = tracker.get_active()

    console.print()
    console.print("[bold]Sessions[/bold]")
    console.print()

    # Header
    console.print(f"{'ID':<16} {'STATUS':<10} {'MODE':<12} {'CREATED':<14} INITIAL PROMPT")
    console.print("-" * 80)

    for session in sessions:
        # Format status color
        status_color = {
            SessionState.RUNNING: "green",
            SessionState.STOPPED: "yellow",
            SessionState.DESTROYED: "red",
        }.get(session.state, "dim")

        created = _format_relative_time(session.created_at)
        prompt_preview = (session.initial_prompt or "-")[:25]
        if session.initial_prompt and len(session.initial_prompt) > 25:
            prompt_preview += "..."

        active_marker = "*" if session.id == active_id else " "

        console.print(
            f"{active_marker}{session.id:<15} "
            f"[{status_color}]{session.state.value:<10}[/{status_color}] "
            f"{session.mode.value:<12} "
            f"{created:<14} "
            f'"{prompt_preview}"'
        )

    console.print()
    if active_id:
        console.print("[dim]* Active session[/dim]")


@session_app.command("attach")
def attach(
    sandbox_id: str | None = typer.Argument(
        None, help="Sandbox ID to attach to (default: last sandbox in this terminal)"
    ),
) -> None:
    """Attach to an existing sandbox.

    Reconnects to a running or stopped sandbox session.
    If no sandbox ID is provided, attaches to the last sandbox created in this terminal.

    Example:
        runtm attach                    # Attach to last sandbox in this terminal
        runtm session attach sbx_abc123 # Attach to specific sandbox
    """
    _require_sandbox()
    provider = LocalSandboxProvider()  # type: ignore[name-defined]
    tracker = ActiveSessionTracker()  # type: ignore[name-defined]

    # If no sandbox_id provided, use terminal-specific active session
    if sandbox_id is None:
        sandbox_id = tracker.get_active(terminal_only=True)
        if sandbox_id is None:
            # Fall back to global active session
            sandbox_id = tracker.get_active()

        if sandbox_id is None:
            console.print("[red]No sandbox to attach to.[/red]")
            console.print("Start one with: [cyan]runtm start[/cyan]")
            console.print("Or specify a sandbox ID: [cyan]runtm attach <sandbox_id>[/cyan]")
            raise typer.Exit(1)

    sandbox = provider.state_store.load(sandbox_id)
    if sandbox is None:
        console.print(f"[red]Sandbox not found: {sandbox_id}[/red]")
        console.print("Run [cyan]runtm session list[/cyan] to see available sandboxes.")
        raise typer.Exit(1)

    # Set as active
    tracker.set_active(sandbox_id)

    console.print(f"[green]✓[/green] Attaching to sandbox [bold]{sandbox_id}[/bold]")
    console.print()

    provider.attach(sandbox_id)

    # Show exit message
    console.print()
    console.print(f"[dim]Left sandbox {sandbox_id}[/dim]")
    console.print()
    console.print("Session is still running. You can:")
    console.print(f"  [cyan]runtm attach {sandbox_id}[/cyan]  - Reattach to this sandbox")
    console.print('  [cyan]runtm prompt "..."[/cyan]        - Send a prompt (autopilot)')
    console.print(f"  [cyan]runtm session stop {sandbox_id}[/cyan] - Stop the session")


@session_app.command("stop")
def stop(
    sandbox_id: str = typer.Argument(..., help="Sandbox ID to stop"),
) -> None:
    """Stop a sandbox (preserves workspace).

    Marks the sandbox as stopped. The workspace and files are preserved.
    You can reattach later with 'runtm session attach'.

    Example:
        runtm session stop sbx_abc123
    """
    _require_sandbox()

    from runtm_shared.types import SessionState

    provider = LocalSandboxProvider()  # type: ignore[name-defined]
    state_store = SandboxStateStore()  # type: ignore[name-defined]

    provider.stop(sandbox_id)

    # Update session state
    session = state_store.load_session(sandbox_id)
    if session:
        session.state = SessionState.STOPPED
        session.updated_at = datetime.now(timezone.utc)
        state_store.save_session(session)

    console.print(f"[green]✓[/green] Sandbox {sandbox_id} stopped")
    console.print(
        f"[dim]Workspace preserved. Reattach with: runtm session attach {sandbox_id}[/dim]"
    )


@session_app.command("destroy")
def destroy(
    sandbox_id: str = typer.Argument(..., help="Sandbox ID to destroy"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Destroy a sandbox and delete workspace.

    Permanently deletes the sandbox and all files in its workspace.
    This action cannot be undone.

    Example:
        runtm session destroy sbx_abc123
        runtm session destroy sbx_abc123 --force  # Skip confirmation
    """
    _require_sandbox()

    if not force:
        if not typer.confirm(f"Destroy sandbox {sandbox_id} and delete all files?", default=False):
            console.print("[dim]Cancelled.[/dim]")
            raise typer.Exit(0)

    provider = LocalSandboxProvider()  # type: ignore[name-defined]
    state_store = SandboxStateStore()  # type: ignore[name-defined]
    tracker = ActiveSessionTracker()  # type: ignore[name-defined]

    provider.destroy(sandbox_id)
    state_store.delete_session(sandbox_id)

    # Clear active if this was it
    if tracker.get_active() == sandbox_id:
        tracker.clear_active()

    console.print(f"[green]✓[/green] Sandbox {sandbox_id} destroyed")


@session_app.command("deploy")
def deploy_from_sandbox(
    sandbox_id: str = typer.Argument(None, help="Sandbox ID (default: most recent)"),
    path: str = typer.Option(".", "--path", "-p", help="Path inside sandbox to deploy"),
) -> None:
    """Deploy what's in the sandbox to a live URL.

    Deploys the code from a sandbox workspace to a production URL.
    Uses the existing runtm deploy infrastructure (Fly.io).

    Example:
        runtm session deploy                    # Deploy most recent sandbox
        runtm session deploy sbx_abc123         # Deploy specific sandbox
        runtm session deploy --path ./backend   # Deploy subdirectory
    """
    _require_sandbox()
    provider = LocalSandboxProvider()  # type: ignore[name-defined]

    # Get sandbox
    if sandbox_id is None:
        sandboxes = provider.list_sandboxes()
        if not sandboxes:
            console.print("[red]No sandboxes found.[/red]")
            raise typer.Exit(1)
        sandbox = sandboxes[-1]  # Most recent
        sandbox_id = sandbox.id
        console.print(f"[dim]Using most recent sandbox: {sandbox_id}[/dim]")
    else:
        sandbox = provider.state_store.load(sandbox_id)
        if sandbox is None:
            console.print(f"[red]Sandbox not found: {sandbox_id}[/red]")
            raise typer.Exit(1)

    # Deploy from workspace
    workspace = Path(sandbox.workspace_path) / path
    if not workspace.exists():
        console.print(f"[red]Path not found: {workspace}[/red]")
        raise typer.Exit(1)

    console.print(f"[dim]Deploying from {workspace}...[/dim]")

    # Use existing deploy logic
    from runtm_cli.commands.deploy import deploy_command

    deploy_command(path=workspace)
