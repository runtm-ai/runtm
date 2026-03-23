"""Sandbox management commands.

Commands:
- runtm sandbox build: Pre-build Docker sandbox images
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import typer
from rich.console import Console

console = Console()

sandbox_app = typer.Typer(name="sandbox", help="Manage sandbox images and environments.")

SUPPORTED_AGENTS = ["claude-code", "codex"]

DOCKERFILE_MAP: dict[str, str] = {
    "claude-code": "Dockerfile.claude-code",
    "codex": "Dockerfile.codex",
}

IMAGE_MAP: dict[str, str] = {
    "claude-code": "runtm-sandbox/claude-code:latest",
    "codex": "runtm-sandbox/codex:latest",
}

DOCKERFILES_DIR = Path(__file__).parent.parent.parent.parent / "sandbox" / "dockerfiles"


def _build_one(agent: str, no_cache: bool = False) -> bool:
    """Build a single sandbox image. Returns True on success."""
    dockerfile = DOCKERFILE_MAP.get(agent)
    image = IMAGE_MAP.get(agent)
    if not dockerfile or not image:
        console.print(f"[red]Unknown agent: {agent}[/red]")
        return False

    dockerfile_path = DOCKERFILES_DIR / dockerfile
    if not dockerfile_path.exists():
        console.print(f"[red]Dockerfile not found: {dockerfile_path}[/red]")
        return False

    console.print(f"\n[bold]Building {image}[/bold]")
    console.print(f"  Dockerfile: {dockerfile_path}")
    console.print()

    cmd = [
        "docker",
        "build",
        "-f",
        str(dockerfile_path),
        "-t",
        image,
    ]
    if no_cache:
        cmd.append("--no-cache")
    cmd.append(str(DOCKERFILES_DIR))

    result = subprocess.run(cmd, stdout=sys.stdout, stderr=sys.stderr)

    if result.returncode == 0:
        size_result = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Size}}", image],
            capture_output=True,
            text=True,
        )
        if size_result.returncode == 0:
            size_bytes = int(size_result.stdout.strip())
            size_mb = size_bytes / (1024 * 1024)
            console.print(f"\n[green]  ✓ {image} built ({size_mb:.0f} MB)[/green]")
        else:
            console.print(f"\n[green]  ✓ {image} built[/green]")
        return True

    console.print(f"\n[red]  ✗ {image} build failed (exit {result.returncode})[/red]")
    return False


@sandbox_app.command("build")
def build(
    agent: str | None = typer.Option(
        None,
        "--agent",
        "-a",
        help=f"Agent image to build ({', '.join(SUPPORTED_AGENTS)})",
    ),
    all_agents: bool = typer.Option(
        False,
        "--all",
        help="Build images for all supported agents",
    ),
    no_cache: bool = typer.Option(
        False,
        "--no-cache",
        help="Force a clean rebuild (no Docker cache)",
    ),
) -> None:
    """Pre-build Docker sandbox images.

    Builds the sandbox Docker image so that `runtm session start` is instant.
    If neither --agent nor --all is specified, builds claude-code by default.

    Examples:
        runtm sandbox build                 # Build claude-code image
        runtm sandbox build --agent codex   # Build codex image
        runtm sandbox build --all           # Build all agent images
        runtm sandbox build --no-cache      # Force clean rebuild
    """
    import shutil

    if not shutil.which("docker"):
        console.print("[red]Docker not found.[/red]")
        console.print()
        console.print(
            "Install Docker Desktop: [cyan]https://www.docker.com/products/docker-desktop[/cyan]"
        )
        raise typer.Exit(1)

    targets = SUPPORTED_AGENTS if all_agents else [agent or "claude-code"]

    for t in targets:
        if t not in SUPPORTED_AGENTS:
            console.print(f"[red]Unknown agent: {t}[/red]")
            console.print(f"Supported: {', '.join(SUPPORTED_AGENTS)}")
            raise typer.Exit(1)

    console.print(f"[bold]Building sandbox image(s):[/bold] {', '.join(targets)}")

    failed: list[str] = []
    for t in targets:
        if not _build_one(t, no_cache=no_cache):
            failed.append(t)

    if failed:
        console.print(f"\n[red]Failed to build: {', '.join(failed)}[/red]")
        raise typer.Exit(1)

    console.print("\n[green]All images built successfully.[/green]")
    console.print("[dim]Run 'runtm start --provider docker' to launch a sandbox.[/dim]")
