"""Init command - scaffold new projects from templates."""

from __future__ import annotations

import shutil
from pathlib import Path

import questionary
import typer
from questionary import Style
from rich.console import Console

console = Console()

# Template definitions with descriptions (ordered list for numbered selection)
TEMPLATES_LIST = [
    {
        "name": "backend-service",
        "title": "Backend Service",
        "description": "Great for scrapers, agent backends, webhooks, and API services",
    },
    {
        "name": "static-site",
        "title": "Static Site",
        "description": "Great for marketing sites, landing pages, docs, and status pages",
    },
    {
        "name": "web-app",
        "title": "Web App",
        "description": "Great for dashboards, customer portals, and AI app demos",
    },
    {
        "name": "docker",
        "title": "Docker (BYOD)",
        "description": "Bring your own Dockerfile - for Go, Rust, Elixir, or any language",
    },
]

# Dict for quick lookup
TEMPLATES = {t["name"]: t for t in TEMPLATES_LIST}

# Custom style for questionary prompts - using emerald green (like "Live" status in dashboard)
PROMPT_STYLE = Style(
    [
        ("qmark", "fg:#10b981 bold"),  # emerald-500
        ("question", "bold"),
        ("answer", "fg:#10b981 bold"),  # emerald-500
        ("pointer", "fg:#10b981 bold"),  # emerald-500
        ("highlighted", "fg:#10b981 bold"),  # emerald-500
        ("selected", "fg:#10b981"),  # emerald-500
        ("separator", "fg:#71717a"),  # zinc-500
        ("instruction", "fg:#71717a"),  # zinc-500
    ]
)


def _find_project_root() -> Path | None:
    """Find the project root directory by looking for pyproject.toml.

    Returns:
        Path to project root or None if not found
    """
    # Start from the current file and walk up
    current = Path(__file__).resolve()
    while current != current.parent:
        if (current / "pyproject.toml").exists() or (current / "templates").exists():
            return current
        current = current.parent
    return None


def prompt_template_selection() -> str:
    """Prompt user to select a template interactively.

    Supports both arrow key navigation and number shortcuts (1, 2, 3) for LLM/agent use.

    Returns:
        Selected template name
    """
    # Build choices with numbered shortcuts for LLM-friendliness
    choices = [
        questionary.Choice(
            title=f"[{i + 1}] {t['title']} - {t['description']}",
            value=t["name"],
            shortcut_key=str(i + 1),  # Allow pressing 1, 2, 3 to select
        )
        for i, t in enumerate(TEMPLATES_LIST)
    ]

    console.print()
    console.print("[dim]Select a template (arrow keys + Enter, or press 1/2/3/4):[/dim]")
    console.print()

    result = questionary.select(
        "What type of project do you want to create?",
        choices=choices,
        style=PROMPT_STYLE,
        instruction="",
        use_shortcuts=True,  # Enable number shortcuts
    ).ask()

    if result is None:
        # User cancelled (Ctrl+C)
        raise typer.Exit(0)

    return result


def get_template_path(template: str) -> Path | None:
    """Get path to template directory.

    Args:
        template: Template name (e.g., "backend-service")

    Returns:
        Path to template or None if not found
    """
    # Try to find project root
    project_root = _find_project_root()
    if project_root:
        template_path = project_root / "templates" / template
        if template_path.exists():
            return template_path

    # Fallback: try relative to current file (for development)
    # init.py is at: packages/cli/runtm_cli/commands/init.py
    # Go up 5 levels to get to project root
    dev_template_path = Path(__file__).parent.parent.parent.parent.parent / "templates" / template
    if dev_template_path.exists():
        return dev_template_path

    # Try current working directory (if running from project root)
    cwd_template_path = Path.cwd() / "templates" / template
    if cwd_template_path.exists():
        return cwd_template_path

    return None


def _get_old_template(dest_path: Path) -> str | None:
    """Detect the old template from existing runtm.yaml.

    Args:
        dest_path: Destination directory

    Returns:
        Old template name or None if not found
    """
    runtm_yaml = dest_path / "runtm.yaml"
    if not runtm_yaml.exists():
        return None

    try:
        import yaml

        with runtm_yaml.open() as f:
            data = yaml.safe_load(f)
            return data.get("template") if isinstance(data, dict) else None
    except Exception:
        return None


def _cleanup_old_template(dest_path: Path, old_template: str) -> None:
    """Remove template-specific files/directories from the old template.

    Args:
        dest_path: Destination directory
        old_template: Name of the old template to clean up
    """
    # Template-specific paths to remove when switching templates
    template_paths = {
        "backend-service": [
            "app",
            "tests",
            "pyproject.toml",
            "Dockerfile",
            "README.md",
            "AGENT.md",
            "CLAUDE.md",
            ".cursor",
        ],
        "static-site": [
            "app",
            "components",
            "public",
            "package.json",
            "package-lock.json",
            "next.config.js",
            "next-env.d.ts",
            "postcss.config.js",
            "tailwind.config.js",
            "tsconfig.json",
            "Dockerfile",
            "README.md",
            "AGENT.md",
            "CLAUDE.md",
            ".cursor",
        ],
        "web-app": [
            "frontend",
            "backend",
            "Dockerfile",
            "README.md",
            "AGENT.md",
            "CLAUDE.md",
            ".cursor",
        ],
        "docker": [
            # Docker template only creates runtm.yaml - user provides everything else
            "runtm.yaml",
        ],
    }

    paths_to_remove = template_paths.get(old_template, [])
    for path_name in paths_to_remove:
        path_to_remove = dest_path / path_name
        if path_to_remove.exists():
            if path_to_remove.is_dir():
                shutil.rmtree(path_to_remove)
            else:
                path_to_remove.unlink()


def _generate_docker_template(dest_path: Path, name: str) -> None:
    """Generate minimal files for docker template.

    Creates only runtm.yaml - the user provides their own Dockerfile.

    Args:
        dest_path: Destination directory
        name: Project name
    """
    # Create runtm.yaml with minimal config
    runtm_yaml = f"""# Runtm Docker Template
# Bring your own Dockerfile - deploy any containerized app

name: {name}
template: docker
port: 8080
health_path: /health
tier: starter

# Optional: declare environment variables your app needs
# env_schema:
#   - name: DATABASE_URL
#     type: string
#     required: true
#     secret: true
"""
    (dest_path / "runtm.yaml").write_text(runtm_yaml)

    # Create a sample Dockerfile if none exists
    dockerfile_path = dest_path / "Dockerfile"
    if not dockerfile_path.exists():
        sample_dockerfile = """# Sample Dockerfile - replace with your own
# This is a minimal example for reference

FROM alpine:latest

# Install your runtime (e.g., go, rust, elixir)
# RUN apk add --no-cache go

# Set working directory
WORKDIR /app

# Copy your application files
COPY . .

# Build your application (if needed)
# RUN go build -o main .

# Expose the port specified in runtm.yaml
EXPOSE 8080

# Health check endpoint (must return HTTP 200)
# Your app should implement GET /health returning 200 OK

# Run your application
# CMD ["./main"]
CMD ["echo", "Replace this Dockerfile with your own!"]
"""
        dockerfile_path.write_text(sample_dockerfile)


def copy_template(
    template_path: Path,
    dest_path: Path,
    name: str | None = None,
    cleanup_old: bool = False,
) -> None:
    """Copy template to destination.

    Args:
        template_path: Source template directory
        dest_path: Destination directory
        name: Optional project name to substitute
        cleanup_old: If True, clean up old template files before copying
    """
    # Clean up old template if requested
    if cleanup_old:
        old_template = _get_old_template(dest_path)
        if old_template and old_template in TEMPLATES:
            _cleanup_old_template(dest_path, old_template)

    # Files that need name substitution
    name_substitution_files = {"runtm.yaml", "package.json", "pyproject.toml"}

    # Copy all files
    for item in template_path.rglob("*"):
        if item.is_file():
            relative = item.relative_to(template_path)
            dest_file = dest_path / relative

            # Create parent directories
            dest_file.parent.mkdir(parents=True, exist_ok=True)

            # Copy file
            shutil.copy2(item, dest_file)

            # Substitute name in config files
            if relative.name in name_substitution_files and name:
                content = dest_file.read_text()
                # Replace all placeholder names
                content = content.replace("my-service", name)
                content = content.replace("my-site", name)
                content = content.replace("my-web-app-frontend", f"{name}-frontend")
                content = content.replace("my-web-app-backend", f"{name}-backend")
                content = content.replace("my-web-app", name)
                dest_file.write_text(content)


def init_command(
    template: str | None = typer.Argument(
        None,
        help="Template type: backend-service, static-site, web-app, or docker",
    ),
    path: Path = typer.Option(
        Path("."),
        "--path",
        "-p",
        help="Destination directory",
    ),
    name: str | None = typer.Option(
        None,
        "--name",
        "-n",
        help="Project name (defaults to directory name)",
    ),
) -> None:
    """Initialize a new project from template.

    Examples:
        runtm init                  # Interactive template selection
        runtm init backend-service  # Creates FastAPI backend service
        runtm init static-site      # Creates Next.js static site
        runtm init web-app          # Creates fullstack app (Next.js + FastAPI)
        runtm init docker           # Minimal runtm.yaml for BYOD projects
    """
    import time

    from runtm_cli.telemetry import (
        command_span,
        emit_init_completed,
        emit_init_template_selected,
    )

    # If no template provided, show interactive selection
    # Handle None, empty string, or the string "None" (Typer edge case)
    if (
        template is None
        or template == ""
        or (isinstance(template, str) and template.lower() == "none")
    ):
        template_name = prompt_template_selection()
    else:
        template_name = template

    start_time = time.time()

    with command_span("init", {"runtm.template": template_name}):
        # Resolve destination
        dest_path = path.resolve()

        # Resolve name early (needed for docker template)
        if not name:
            name = dest_path.name.lower().replace("_", "-")

        # Docker template: generate minimal files instead of copying
        is_docker_template = template_name == "docker"

        # Get template path (None for docker template)
        template_path = None if is_docker_template else get_template_path(template_name)

        if not template_path and not is_docker_template:
            console.print(f"[red]✗[/red] Template not found: {template_name}")
            console.print()
            console.print("Available templates:")
            for i, t in enumerate(TEMPLATES_LIST):
                console.print(f"  [{i + 1}] [bold]{t['name']}[/bold] - {t['description']}")
            console.print()
            console.print("[dim]Tip: Run [bold]runtm init[/bold] for interactive selection[/dim]")
            console.print(
                "[dim]  or [bold]runtm init backend-service[/bold] to specify directly[/dim]"
            )
            raise typer.Exit(1)

        # Check if destination has files
        has_existing_files = dest_path.exists() and any(dest_path.iterdir())
        should_cleanup = False
        if has_existing_files:
            # Check for existing runtm.yaml
            if (dest_path / "runtm.yaml").exists():
                old_template = _get_old_template(dest_path)
                if old_template and old_template != template_name:
                    console.print(
                        f"[yellow]⚠[/yellow] Project already initialized with template: {old_template}"
                    )
                    console.print(f"Switching to template: {template_name}")
                    if not typer.confirm(
                        "Overwrite existing files? (old template files will be removed)"
                    ):
                        raise typer.Exit(0)
                    should_cleanup = True
                else:
                    console.print(
                        "[yellow]⚠[/yellow] Project already initialized (runtm.yaml exists)"
                    )
                    if not typer.confirm("Overwrite existing files?"):
                        raise typer.Exit(0)

        # Emit template selected event
        emit_init_template_selected(template_name, has_existing_files)

        console.print(f"Initializing {template_name} project...")
        console.print(f"  Path: {dest_path}")
        console.print(f"  Name: {name}")
        console.print()

        # Initialize based on template type
        try:
            dest_path.mkdir(parents=True, exist_ok=True)

            if is_docker_template:
                # Docker template: generate minimal files
                if should_cleanup:
                    old_template = _get_old_template(dest_path)
                    if old_template and old_template in TEMPLATES:
                        _cleanup_old_template(dest_path, old_template)
                _generate_docker_template(dest_path, name)
            else:
                # Standard templates: copy from template directory
                copy_template(template_path, dest_path, name, cleanup_old=should_cleanup)
        except Exception as e:
            console.print(f"[red]✗[/red] Failed to initialize: {e}")
            raise typer.Exit(1)

        # Emit init completed event
        duration_ms = (time.time() - start_time) * 1000
        emit_init_completed(template_name, duration_ms)

        console.print("[green]✓[/green] Project initialized!")
        console.print()

        if is_docker_template:
            console.print("[dim]Docker template initialized with minimal runtm.yaml[/dim]")
            console.print("[dim]Edit the Dockerfile to add your application[/dim]")
        else:
            console.print("[dim]AI assistant rules auto-configured for:[/dim]")
            console.print("[dim]  Cursor, Claude Code, Windsurf, GitHub Copilot[/dim]")

        console.print()
        console.print("Next steps:")
        # Only show cd if destination is different from current directory
        if dest_path.resolve() != Path.cwd().resolve():
            console.print(f"  cd {dest_path.name}")
        if is_docker_template:
            console.print("  # Edit Dockerfile with your application")
        console.print("  runtm validate")
        console.print("  runtm deploy")
