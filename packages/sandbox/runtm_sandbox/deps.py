"""Dependency checking and installation for sandbox runtime.

Handles lazy installation of:
- Bun (or Node.js as fallback)
- sandbox-runtime (@anthropic-ai/sandbox-runtime)
- Claude Code CLI
- bubblewrap (Linux only)
"""

from __future__ import annotations

import platform
import shutil
import subprocess

import structlog

logger = structlog.get_logger()


def check_bun() -> bool:
    """Check if Bun or Node.js is available.

    Returns:
        True if bun or node is found in PATH.
    """
    return shutil.which("bun") is not None or shutil.which("node") is not None


def check_srt() -> bool:
    """Check if sandbox-runtime (srt) is installed.

    Returns:
        True if srt binary is found in PATH.
    """
    return shutil.which("srt") is not None


def check_claude() -> bool:
    """Check if Claude Code CLI is installed.

    Returns:
        True if claude binary is found in PATH.
    """
    return shutil.which("claude") is not None


def check_bwrap() -> bool:
    """Check if bubblewrap is installed (Linux only).

    Returns:
        True if bwrap binary is found in PATH.
    """
    return shutil.which("bwrap") is not None


def get_missing_deps() -> list[tuple[str, callable]]:
    """Get list of missing dependencies.

    Returns:
        List of (name, install_function) tuples for missing deps.
    """
    missing: list[tuple[str, callable]] = []

    if not check_bun():
        missing.append(("Bun runtime", install_bun))

    if not check_srt():
        missing.append(("sandbox-runtime", install_srt))

    if not check_claude():
        missing.append(("Claude Code CLI", install_claude))

    # Only check for bubblewrap on Linux
    if platform.system() == "Linux" and not check_bwrap():
        missing.append(("bubblewrap", install_bwrap))

    return missing


def install_bun() -> bool:
    """Install Bun - single binary, faster than npm.

    Uses the official bun.sh installer script.

    Returns:
        True if installation succeeded.
    """
    logger.info("Installing Bun runtime")
    try:
        result = subprocess.run(
            ["sh", "-c", "curl -fsSL https://bun.sh/install | bash"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            logger.info("Bun installed successfully")
            return True
        else:
            logger.error("Bun installation failed", stderr=result.stderr)
            return False
    except Exception as e:
        logger.error("Bun installation error", error=str(e))
        return False


def install_srt() -> bool:
    """Install sandbox-runtime from npm.

    Prefers bun (faster) over npm.

    Returns:
        True if installation succeeded.
    """
    logger.info("Installing sandbox-runtime")

    # Prefer bun, fall back to npm
    if shutil.which("bun"):
        cmd = ["bun", "install", "-g", "@anthropic-ai/sandbox-runtime"]
    elif shutil.which("npm"):
        cmd = ["npm", "install", "-g", "@anthropic-ai/sandbox-runtime"]
    else:
        logger.error("No package manager available (need bun or npm)")
        return False

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            logger.info("sandbox-runtime installed successfully")
            return True
        else:
            logger.error("sandbox-runtime installation failed", stderr=result.stderr)
            return False
    except Exception as e:
        logger.error("sandbox-runtime installation error", error=str(e))
        return False


def install_claude() -> bool:
    """Install Claude Code CLI using native installer.

    Uses the official claude.ai installer script which handles
    platform detection, PATH setup, etc.

    Returns:
        True if installation succeeded.
    """
    logger.info("Installing Claude Code CLI")
    try:
        result = subprocess.run(
            ["sh", "-c", "curl -fsSL https://claude.ai/install.sh | bash"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            logger.info("Claude Code CLI installed successfully")
            return True
        else:
            logger.error("Claude Code CLI installation failed", stderr=result.stderr)
            return False
    except Exception as e:
        logger.error("Claude Code CLI installation error", error=str(e))
        return False


def install_bwrap() -> bool:
    """Install bubblewrap (Linux only).

    Detects the package manager and installs accordingly.

    Returns:
        True if installation succeeded.
    """
    logger.info("Installing bubblewrap")

    # Detect package manager and install
    if shutil.which("apt"):
        cmd = ["sudo", "apt", "install", "-y", "bubblewrap"]
    elif shutil.which("dnf"):
        cmd = ["sudo", "dnf", "install", "-y", "bubblewrap"]
    elif shutil.which("pacman"):
        cmd = ["sudo", "pacman", "-S", "--noconfirm", "bubblewrap"]
    elif shutil.which("apk"):
        cmd = ["sudo", "apk", "add", "bubblewrap"]
    else:
        logger.error("No supported package manager found for bubblewrap")
        return False

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            logger.info("bubblewrap installed successfully")
            return True
        else:
            logger.error("bubblewrap installation failed", stderr=result.stderr)
            return False
    except Exception as e:
        logger.error("bubblewrap installation error", error=str(e))
        return False


def ensure_sandbox_deps(
    auto_install: bool = True,
    _skip_install: bool = False,
    *,
    auto_approve: bool | None = None,
    _skip_prompt: bool = False,
) -> bool:
    """Ensure all sandbox dependencies are installed.

    By default, automatically installs missing dependencies for a seamless
    first-run experience. Use auto_install=False to check only.

    Args:
        auto_install: If True (default), install missing deps automatically.
        _skip_install: Internal flag for testing - skip installation.
        auto_approve: Backward-compatible alias for auto_install.
        _skip_prompt: Backward-compatible alias for _skip_install.

    Returns:
        True if all dependencies are available (installed or already present).
    """
    if auto_approve is not None:
        auto_install = auto_approve
    if _skip_prompt:
        _skip_install = True

    missing = get_missing_deps()

    if not missing:
        logger.debug("All sandbox dependencies present")
        return True

    missing_names = [name for name, _ in missing]
    logger.info("Missing sandbox dependencies", missing=missing_names)

    # If not auto-installing, return False (caller handles the error)
    if not auto_install or _skip_install:
        return False

    # Auto-install missing dependencies
    print(f"Installing missing dependencies: {', '.join(missing_names)}...")

    for name, install_fn in missing:
        print(f"  Installing {name}...")
        if not install_fn():
            logger.error("Failed to install dependency", name=name)
            return False

    # Verify all deps are now present
    still_missing = get_missing_deps()
    if still_missing:
        logger.error(
            "Some dependencies still missing after installation",
            missing=[name for name, _ in still_missing],
        )
        return False

    print("All dependencies installed successfully!")
    logger.info("All sandbox dependencies installed successfully")
    return True
