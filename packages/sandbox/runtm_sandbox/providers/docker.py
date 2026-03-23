"""Docker-based sandbox provider.

Runs AI coding agents (Claude Code, Codex) inside Docker containers.
Works on any OS with Docker Desktop -- no host tools required beyond Docker.

The container mirrors the E2B template layout: same base image, same tools
pre-installed.  Agent CLIs are downloaded at Docker build time (not
redistributed).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import structlog
from runtm_shared.types import AgentType, Sandbox, SandboxConfig, SandboxState

from ..state import SandboxStateStore
from .base import SandboxProvider

logger = structlog.get_logger()

AGENT_IMAGE_MAP: dict[str, str] = {
    AgentType.CLAUDE_CODE.value: "runtm-sandbox/claude-code",
    AgentType.CODEX.value: "runtm-sandbox/codex",
}

AGENT_DOCKERFILE_MAP: dict[str, str] = {
    AgentType.CLAUDE_CODE.value: "Dockerfile.claude-code",
    AgentType.CODEX.value: "Dockerfile.codex",
}

AGENT_API_KEY_ENV: dict[str, str] = {
    AgentType.CLAUDE_CODE.value: "ANTHROPIC_API_KEY",
    AgentType.CODEX.value: "OPENAI_API_KEY",
}

CONTAINER_PREFIX = "runtm-sbx"


class DockerSandboxProvider(SandboxProvider):
    """Docker-based sandbox using Docker Desktop / Docker Engine.

    Creates isolated containers with AI coding agents pre-installed.
    Works identically on Windows, macOS, and Linux.
    """

    def __init__(self, sandboxes_dir: Path | None = None) -> None:
        if sandboxes_dir is None:
            sandboxes_dir = Path.home() / ".runtm" / "sandboxes"

        self.sandboxes_dir = sandboxes_dir
        self.sandboxes_dir.mkdir(parents=True, exist_ok=True)

        self.state_store = SandboxStateStore(state_dir=sandboxes_dir)

        self._dockerfiles_dir = Path(__file__).parent.parent.parent / "dockerfiles"

    @staticmethod
    def is_available() -> bool:
        """Return True if Docker CLI is on PATH."""
        return shutil.which("docker") is not None

    def _container_name(self, sandbox_id: str) -> str:
        return f"{CONTAINER_PREFIX}-{sandbox_id}"

    def _image_for_agent(self, agent: AgentType) -> str:
        image = AGENT_IMAGE_MAP.get(agent.value)
        if image is None:
            raise ValueError(
                f"No Docker image defined for agent '{agent.value}'. "
                f"Supported: {', '.join(AGENT_IMAGE_MAP)}"
            )
        return f"{image}:latest"

    def _image_exists(self, image: str) -> bool:
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
        )
        return result.returncode == 0

    def _build_image(self, agent: AgentType) -> None:
        """Build the sandbox image for the given agent."""
        dockerfile = AGENT_DOCKERFILE_MAP.get(agent.value)
        if dockerfile is None:
            raise ValueError(f"No Dockerfile for agent '{agent.value}'")

        dockerfile_path = self._dockerfiles_dir / dockerfile
        if not dockerfile_path.exists():
            raise FileNotFoundError(
                f"Dockerfile not found at {dockerfile_path}. "
                "Run 'runtm sandbox build' to set up sandbox images."
            )

        image = self._image_for_agent(agent)
        logger.info("Building sandbox image", image=image, dockerfile=str(dockerfile_path))

        print(f"\n  Building sandbox image ({image})...")
        print(f"  This downloads the {agent.value} CLI and may take 2-5 min on first run.\n")

        result = subprocess.run(
            [
                "docker",
                "build",
                "-f",
                str(dockerfile_path),
                "-t",
                image,
                str(self._dockerfiles_dir),
            ],
            stdout=sys.stdout,
            stderr=sys.stderr,
        )

        if result.returncode != 0:
            raise RuntimeError(f"Docker build failed (exit {result.returncode})")

        logger.info("Sandbox image built", image=image)

    def create(self, sandbox_id: str, config: SandboxConfig) -> Sandbox:
        """Create a new Docker sandbox container."""
        logger.info("Creating Docker sandbox", sandbox_id=sandbox_id, agent=config.agent.value)

        image = self._image_for_agent(config.agent)
        if not self._image_exists(image):
            self._build_image(config.agent)

        workspace = self.sandboxes_dir / sandbox_id / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)

        if config.template:
            self._scaffold_template(workspace, config.template)

        container_name = self._container_name(sandbox_id)
        env_key = AGENT_API_KEY_ENV.get(config.agent.value, "ANTHROPIC_API_KEY")
        api_key = os.environ.get(env_key, "")

        cmd: list[str] = [
            "docker",
            "run",
            "-d",
            "--name",
            container_name,
            "--label",
            "runtm.sandbox=true",
            "--label",
            f"runtm.sandbox.id={sandbox_id}",
            "--label",
            f"runtm.agent={config.agent.value}",
            "-e",
            f"{env_key}={api_key}" if api_key else f"{env_key}=",
            "-e",
            f"RUNTM_SANDBOX={sandbox_id}",
            "-v",
            f"{workspace.resolve()}:/home/user",
            "-w",
            "/home/user",
        ]

        for container_port, host_port in config.port_mappings.items():
            cmd.extend(["-p", f"{host_port}:{container_port}"])

        cmd.append(image)

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Failed to start container: {result.stderr.strip()}")

        sandbox = Sandbox(
            id=sandbox_id,
            session_id=sandbox_id,
            config=config,
            state=SandboxState.RUNNING,
            workspace_path=str(workspace),
        )
        self.state_store.save(sandbox)

        logger.info("Docker sandbox created", sandbox_id=sandbox_id, container=container_name)
        return sandbox

    def attach(self, sandbox_id: str) -> int:
        """Attach to a running Docker sandbox."""
        sandbox = self.state_store.load(sandbox_id)
        if sandbox is None:
            raise ValueError(f"Sandbox not found: {sandbox_id}")

        container_name = self._container_name(sandbox_id)
        logger.info("Attaching to Docker sandbox", sandbox_id=sandbox_id, container=container_name)

        short_id = sandbox_id.replace("sbx_", "")[:8]
        agent_name = sandbox.config.agent.value
        env_key = AGENT_API_KEY_ENV.get(agent_name, "ANTHROPIC_API_KEY")

        banner = f"""
\033[32m╭─────────────────────────────────────────────────────────────╮
│  \033[1mRuntm Docker Sandbox\033[0m\033[32m                                       │
│                                                             │
│  ID:    {sandbox_id:<50}│
│  Agent: {agent_name:<50}│
│  Type '{agent_name.split("-")[0]}' to start the agent{" " * (34 - len(agent_name.split("-")[0]))}│
│  Type 'exit' to leave (container persists)                  │
╰─────────────────────────────────────────────────────────────╯\033[0m
"""
        print(banner)

        api_key = os.environ.get(env_key, "")
        if not api_key:
            print(f"\033[33m  ⚠  {env_key} is not set in your host environment.\033[0m")
            print(f"    Set it before starting:  export {env_key}=sk-...")
            print(f"    Or inside the sandbox:   export {env_key}=sk-...\n")

        ps1 = f"\\[\\033[32m\\][sandbox:{short_id}]\\[\\033[0m\\] \\w $ "

        result = subprocess.run(
            [
                "docker",
                "exec",
                "-it",
                "-e",
                f"PS1={ps1}",
                "-e",
                f"{env_key}={api_key}" if api_key else f"{env_key}=",
                container_name,
                "/bin/bash",
                "--norc",
                "--noprofile",
            ],
        )

        logger.info(
            "Docker sandbox session ended", sandbox_id=sandbox_id, exit_code=result.returncode
        )
        return result.returncode

    def stop(self, sandbox_id: str) -> None:
        """Stop a Docker sandbox container (workspace preserved)."""
        container_name = self._container_name(sandbox_id)

        subprocess.run(["docker", "stop", container_name], capture_output=True)

        sandbox = self.state_store.load(sandbox_id)
        if sandbox:
            sandbox.state = SandboxState.STOPPED
            self.state_store.save(sandbox)

        logger.info("Docker sandbox stopped", sandbox_id=sandbox_id)

    def destroy(self, sandbox_id: str) -> None:
        """Destroy a Docker sandbox: remove container and workspace."""
        container_name = self._container_name(sandbox_id)
        logger.info("Destroying Docker sandbox", sandbox_id=sandbox_id)

        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)

        sandbox_dir = self.sandboxes_dir / sandbox_id
        if sandbox_dir.exists():
            shutil.rmtree(sandbox_dir)

        self.state_store.delete(sandbox_id)
        logger.info("Docker sandbox destroyed", sandbox_id=sandbox_id)

    def list_sandboxes(self) -> list[Sandbox]:
        """List all Docker sandboxes (running + stopped from state store)."""
        return self.state_store.list_all()

    def get_state(self, sandbox_id: str) -> SandboxState:
        """Get sandbox state, cross-referencing Docker container status."""
        sandbox = self.state_store.load(sandbox_id)
        if sandbox is None:
            return SandboxState.DESTROYED

        container_name = self._container_name(sandbox_id)
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", container_name],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            return SandboxState.DESTROYED

        docker_status = result.stdout.strip()
        if docker_status == "running":
            return SandboxState.RUNNING
        if docker_status in ("exited", "dead", "paused"):
            return SandboxState.STOPPED
        return sandbox.state

    def _scaffold_template(self, workspace: Path, template: str) -> None:
        """Copy template files into workspace (same logic as LocalSandboxProvider)."""
        logger.info("Scaffolding template", template=template, workspace=str(workspace))

        possible_paths = [
            Path(__file__).parent.parent.parent.parent.parent / "templates" / template,
            Path(__file__).parent.parent / "templates" / template,
        ]

        template_path = None
        for path in possible_paths:
            if path.exists():
                template_path = path
                break

        if template_path is None:
            logger.warning("Template not found", template=template)
            return

        shutil.copytree(template_path, workspace, dirs_exist_ok=True)
        logger.info("Template scaffolded", template=template)
