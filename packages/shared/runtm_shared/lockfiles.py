"""Lockfile detection and validation for deployment determinism.

This module provides utilities to detect, validate, and fix lockfile status
across different package managers (bun, npm, uv, poetry).

Design principles:
- Autofix in `runtm run` (sandbox is disposable)
- Block in `runtm deploy` unless --yes (prod must be reproducible)
- One package manager per ecosystem (don't support multiple equally)
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

PackageManager = Literal["bun", "npm", "uv", "poetry", "pip"]


@dataclass
class LockfileStatus:
    """Status of lockfile for a project.

    Attributes:
        exists: Whether the lockfile exists
        synced: Whether the lockfile is in sync with the manifest
                (True if frozen install would succeed)
        manager: The detected package manager
        lockfile_path: Path to the lockfile (relative to project root)
        install_cmd: Command to install dependencies (updates lockfile)
        frozen_cmd: Command to install with frozen lockfile (fails if drifted)
    """

    exists: bool
    synced: bool
    manager: PackageManager
    lockfile_path: str
    install_cmd: str
    frozen_cmd: str

    @property
    def needs_fix(self) -> bool:
        """Whether the lockfile needs to be fixed."""
        return not self.exists or not self.synced


def _check_command_exists(cmd: str) -> bool:
    """Check if a command exists in PATH."""
    return shutil.which(cmd) is not None


def _run_frozen_install(path: Path, frozen_cmd: str, timeout: int = 120) -> bool:
    """Run a frozen install command and return True if it succeeds.

    This is the robust way to check if a lockfile is in sync:
    if frozen install fails, the lockfile is drifted.
    """
    try:
        result = subprocess.run(
            frozen_cmd,
            shell=True,
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        # Timeout means something is wrong, treat as not synced
        return False
    except Exception:
        return False


def check_node_lockfile(path: Path) -> LockfileStatus:
    """Check Node.js lockfile status.

    Priority: bun > npm (we pick one, not multiple)
    """
    # Check for bun first (preferred). Bun 1.2+ writes a text bun.lock by
    # default; older versions write the binary bun.lockb.
    bun_lock = next(
        (path / name for name in ("bun.lock", "bun.lockb") if (path / name).exists()),
        None,
    )
    package_json = path / "package.json"

    if not package_json.exists():
        # No Node.js project
        return LockfileStatus(
            exists=False,
            synced=False,
            manager="npm",
            lockfile_path="package-lock.json",
            install_cmd="npm install",
            frozen_cmd="npm ci",
        )

    # Detect which lockfile exists
    if bun_lock is not None:
        manager: PackageManager = "bun"
        lockfile_path = bun_lock.name
        install_cmd = "bun install"
        frozen_cmd = "bun install --frozen-lockfile"
        exists = True
    elif (path / "package-lock.json").exists():
        manager = "npm"
        lockfile_path = "package-lock.json"
        install_cmd = "npm install"
        frozen_cmd = "npm ci"
        exists = True
    else:
        # No lockfile - default to bun if available, else npm
        if _check_command_exists("bun"):
            manager = "bun"
            lockfile_path = "bun.lockb"
            install_cmd = "bun install"
            frozen_cmd = "bun install --frozen-lockfile"
        else:
            manager = "npm"
            lockfile_path = "package-lock.json"
            install_cmd = "npm install"
            frozen_cmd = "npm ci"
        exists = False

    # Check if synced (only if lockfile exists)
    synced = False
    if exists and _check_command_exists(manager):
        synced = _run_frozen_install(path, frozen_cmd)

    return LockfileStatus(
        exists=exists,
        synced=synced,
        manager=manager,
        lockfile_path=lockfile_path,
        install_cmd=install_cmd,
        frozen_cmd=frozen_cmd,
    )


def check_python_lockfile(path: Path) -> LockfileStatus:
    """Check Python lockfile status.

    Priority: uv > poetry > pip (we pick one, not multiple)
    """
    pyproject = path / "pyproject.toml"

    if not pyproject.exists():
        # No Python project
        return LockfileStatus(
            exists=False,
            synced=False,
            manager="pip",
            lockfile_path="requirements.lock",
            install_cmd="pip install -e .",
            frozen_cmd="pip install -e .",
        )

    # Detect which lockfile exists
    uv_lock = path / "uv.lock"
    poetry_lock = path / "poetry.lock"

    if uv_lock.exists():
        manager: PackageManager = "uv"
        lockfile_path = "uv.lock"
        install_cmd = "uv sync"
        frozen_cmd = "uv sync --frozen"
        exists = True
    elif poetry_lock.exists():
        manager = "poetry"
        lockfile_path = "poetry.lock"
        install_cmd = "poetry lock --no-update"
        frozen_cmd = "poetry check --lock"
        exists = True
    else:
        # No lockfile - default to uv if available, else poetry, else pip
        if _check_command_exists("uv"):
            manager = "uv"
            lockfile_path = "uv.lock"
            install_cmd = "uv sync"
            frozen_cmd = "uv sync --frozen"
        elif _check_command_exists("poetry"):
            manager = "poetry"
            lockfile_path = "poetry.lock"
            install_cmd = "poetry lock"
            frozen_cmd = "poetry check --lock"
        else:
            manager = "pip"
            lockfile_path = "requirements.lock"
            install_cmd = "pip install -e ."
            frozen_cmd = "pip install -e ."
        exists = False

    # Check if synced (only if lockfile exists)
    synced = False
    if exists and _check_command_exists(manager):
        synced = _run_frozen_install(path, frozen_cmd)

    return LockfileStatus(
        exists=exists,
        synced=synced,
        manager=manager,
        lockfile_path=lockfile_path,
        install_cmd=install_cmd,
        frozen_cmd=frozen_cmd,
    )


def check_lockfile(path: Path, runtime: str) -> LockfileStatus:
    """Check lockfile status for a project.

    Args:
        path: Path to project directory
        runtime: Runtime type from manifest (python, node, fullstack)

    Returns:
        LockfileStatus with detection results
    """
    if runtime == "python":
        return check_python_lockfile(path)
    elif runtime == "node":
        return check_node_lockfile(path)
    elif runtime == "fullstack":
        # For fullstack, check both and return the one that needs fixing
        # Priority: frontend (node) issues first, then backend (python)
        frontend_path = path / "frontend"
        backend_path = path / "backend"

        # Check frontend first
        if frontend_path.exists():
            frontend_status = check_node_lockfile(frontend_path)
            if frontend_status.needs_fix:
                # Adjust paths to be relative to project root
                return LockfileStatus(
                    exists=frontend_status.exists,
                    synced=frontend_status.synced,
                    manager=frontend_status.manager,
                    lockfile_path=f"frontend/{frontend_status.lockfile_path}",
                    install_cmd=f"cd frontend && {frontend_status.install_cmd}",
                    frozen_cmd=f"cd frontend && {frontend_status.frozen_cmd}",
                )

        # Check backend
        if backend_path.exists():
            backend_status = check_python_lockfile(backend_path)
            if backend_status.needs_fix:
                return LockfileStatus(
                    exists=backend_status.exists,
                    synced=backend_status.synced,
                    manager=backend_status.manager,
                    lockfile_path=f"backend/{backend_status.lockfile_path}",
                    install_cmd=f"cd backend && {backend_status.install_cmd}",
                    frozen_cmd=f"cd backend && {backend_status.frozen_cmd}",
                )

        # Both are fine, return frontend status
        if frontend_path.exists():
            frontend_status = check_node_lockfile(frontend_path)
            return LockfileStatus(
                exists=frontend_status.exists,
                synced=frontend_status.synced,
                manager=frontend_status.manager,
                lockfile_path=f"frontend/{frontend_status.lockfile_path}",
                install_cmd=f"cd frontend && {frontend_status.install_cmd}",
                frozen_cmd=f"cd frontend && {frontend_status.frozen_cmd}",
            )

        # Fallback
        return check_node_lockfile(path)
    else:
        # Unknown runtime, try to detect
        if (path / "package.json").exists():
            return check_node_lockfile(path)
        elif (path / "pyproject.toml").exists():
            return check_python_lockfile(path)
        else:
            # Default to node
            return check_node_lockfile(path)


def check_all_lockfiles(path: Path, runtime: str) -> list[LockfileStatus]:
    """Check all lockfiles for a fullstack project.

    For non-fullstack projects, returns a single-item list.
    For fullstack projects, returns status for both frontend and backend.

    Args:
        path: Path to project directory
        runtime: Runtime type from manifest

    Returns:
        List of LockfileStatus objects
    """
    if runtime != "fullstack":
        return [check_lockfile(path, runtime)]

    results = []
    frontend_path = path / "frontend"
    backend_path = path / "backend"

    if frontend_path.exists():
        frontend_status = check_node_lockfile(frontend_path)
        results.append(
            LockfileStatus(
                exists=frontend_status.exists,
                synced=frontend_status.synced,
                manager=frontend_status.manager,
                lockfile_path=f"frontend/{frontend_status.lockfile_path}",
                install_cmd=f"cd frontend && {frontend_status.install_cmd}",
                frozen_cmd=f"cd frontend && {frontend_status.frozen_cmd}",
            )
        )

    if backend_path.exists():
        backend_status = check_python_lockfile(backend_path)
        results.append(
            LockfileStatus(
                exists=backend_status.exists,
                synced=backend_status.synced,
                manager=backend_status.manager,
                lockfile_path=f"backend/{backend_status.lockfile_path}",
                install_cmd=f"cd backend && {backend_status.install_cmd}",
                frozen_cmd=f"cd backend && {backend_status.frozen_cmd}",
            )
        )

    return results


def fix_lockfile(path: Path, status: LockfileStatus, timeout: int = 300) -> bool:
    """Fix a lockfile by running the install command.

    Args:
        path: Path to project directory
        status: LockfileStatus from check_lockfile
        timeout: Timeout in seconds for the install command

    Returns:
        True if the fix succeeded, False otherwise
    """
    try:
        result = subprocess.run(
            status.install_cmd,
            shell=True,
            cwd=str(path),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    except Exception:
        return False
