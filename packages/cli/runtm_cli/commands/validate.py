"""Validate command - local project validation."""

from __future__ import annotations

import ast
import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console

from runtm_shared import Limits, Manifest, create_validation_result

console = Console()

# Bump this when validation logic changes to invalidate cache
VALIDATOR_VERSION = "2"  # v2: Added ESLint + TypeScript validation for Node.js projects

# Cache location for successful validations
VALIDATION_CACHE_DIR = Path.home() / ".cache" / "runtm" / "validation"


def _compute_validation_cache_key(project_path: Path, pyproject: Path) -> str:
    """Compute cache key including deps + environment + validator version.

    The cache key includes:
    - pyproject.toml content
    - Lockfile content (uv.lock, poetry.lock, requirements.txt, or requirements.lock)
    - Python version (major.minor)
    - Platform (Darwin, Linux, Windows)
    - Architecture (x86_64, arm64)
    - Validator version (to invalidate cache when logic changes)

    This ensures cached validation results are only used when all factors match.

    Args:
        project_path: Path to project directory (for lockfile lookup)
        pyproject: Path to pyproject.toml

    Returns:
        24-character hash string for cache key
    """
    h = hashlib.sha256()

    # Include pyproject.toml content
    if pyproject.exists():
        h.update(pyproject.read_bytes())

    # Include lockfile content (check in priority order)
    lockfile_names = ["uv.lock", "poetry.lock", "requirements.txt", "requirements.lock"]
    for lockfile_name in lockfile_names:
        lockfile = project_path / lockfile_name
        if lockfile.exists():
            h.update(lockfile.read_bytes())
            break

    # Include Python version (major.minor)
    h.update(f"{sys.version_info.major}.{sys.version_info.minor}".encode())

    # Include platform info
    h.update(platform.system().encode())  # Darwin, Linux, Windows
    h.update(platform.machine().encode())  # x86_64, arm64

    # Include validator version to invalidate cache when logic changes
    h.update(VALIDATOR_VERSION.encode())

    return h.hexdigest()[:24]


def _check_validation_cache(cache_key: str) -> bool:
    """Check if a successful validation exists in cache.

    Args:
        cache_key: Cache key from _compute_validation_cache_key

    Returns:
        True if cached success exists, False otherwise
    """
    cache_file = VALIDATION_CACHE_DIR / f"{cache_key}.validated"
    return cache_file.exists()


def _write_validation_cache(cache_key: str) -> None:
    """Write successful validation to cache.

    Only call this after a successful validation (no errors).
    Never cache failures to avoid "why won't it revalidate?" confusion.

    Args:
        cache_key: Cache key from _compute_validation_cache_key
    """
    try:
        VALIDATION_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = VALIDATION_CACHE_DIR / f"{cache_key}.validated"
        cache_file.write_text(datetime.now().isoformat())
    except Exception:
        pass  # Don't fail validation due to cache write errors


def validate_project(
    path: Path,
    skip_validation: bool = False,
    force_validation: bool = False,
) -> tuple[bool, list[str], list[str]]:
    """Validate a project before deployment.

    This validation is for **developer experience** - catching obvious errors early.
    It is NOT a correctness proof for production. Local imports can pass while
    container build fails (different OS/glibc, Python version, missing system packages).

    Args:
        path: Path to project directory
        skip_validation: Skip Python import validation entirely (faster but riskier)
        force_validation: Force re-validation even if cached

    Returns:
        Tuple of (is_valid, errors, warnings)
    """
    result = create_validation_result()
    manifest = None

    # Check runtm.yaml exists
    manifest_path = path / "runtm.yaml"
    if not manifest_path.exists():
        result.add_error(
            "Missing runtm.yaml - run `runtm init backend-service`, `runtm init static-site`, or `runtm init web-app`"
        )
    else:
        # Validate manifest
        try:
            manifest = Manifest.from_file(manifest_path)

            # Validate health configuration
            health_errors, health_warnings = validate_health_config(manifest)
            for error in health_errors:
                result.add_error(error)
            for warning in health_warnings:
                result.add_warning(warning)
        except Exception as e:
            result.add_error(f"Invalid runtm.yaml: {e}")

    # Check Dockerfile exists
    dockerfile_path = path / "Dockerfile"
    if not dockerfile_path.exists():
        result.add_error("Missing Dockerfile")
    else:
        # Validate Dockerfile cache hygiene for optimal layer caching
        cache_warnings = validate_dockerfile_cache_hygiene(dockerfile_path)
        for warning in cache_warnings:
            result.add_warning(warning)

    # Check artifact size
    total_size = 0
    file_count = 0
    exclude_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv", ".next", "out"}

    for item in path.rglob("*"):
        # Skip excluded directories
        if any(excluded in item.parts for excluded in exclude_dirs):
            continue
        if item.is_file():
            total_size += item.stat().st_size
            file_count += 1

    if total_size > Limits.MAX_ARTIFACT_SIZE_BYTES:
        size_mb = total_size / (1024 * 1024)
        limit_mb = Limits.MAX_ARTIFACT_SIZE_BYTES / (1024 * 1024)
        result.add_error(
            f"Project too large: {size_mb:.1f} MB > {limit_mb:.0f} MB limit. "
            "Remove large files or add them to .gitignore"
        )

    # Check for common issues
    if (path / ".env").exists():
        result.add_warning(".env file found - it will be excluded from deployment")

    # Check for discovery metadata
    discovery_path = path / "runtm.discovery.yaml"
    if not discovery_path.exists():
        result.add_warning(
            "No runtm.discovery.yaml found. "
            "Add app metadata for better discoverability in the dashboard."
        )
    else:
        # Check if discovery file has unfilled TODO placeholders
        try:
            discovery_content = discovery_path.read_text()
            if "# TODO:" in discovery_content or "TODO:" in discovery_content:
                result.add_warning(
                    "runtm.discovery.yaml has unfilled TODO placeholders. "
                    "Fill them in before deploying for better app discoverability."
                )
        except Exception:
            pass  # Don't block on read errors

    # Docker template: skip runtime-specific validation (bring your own Dockerfile)
    is_docker_template = manifest and manifest.template == "docker"

    if is_docker_template:
        # Docker template only validates: runtm.yaml + Dockerfile + artifact size
        # Skip Python/Node.js specific validation
        pass
    else:
        # Detect project type based on manifest or file structure
        is_node_project = (path / "package.json").exists()
        is_python_project = (path / "pyproject.toml").exists() or (
            path / "requirements.txt"
        ).exists()

        # Python-specific validation (backend-service template)
        if is_python_project and not is_node_project:
            if (path / "requirements.txt").exists() and not (path / "pyproject.toml").exists():
                result.add_warning("Using requirements.txt without pyproject.toml")

            # Validate Python syntax for all .py files
            python_errors = validate_python_syntax(path, exclude_dirs)
            for error in python_errors:
                result.add_error(error)

            # Validate Python imports with production dependencies
            import_errors, import_warnings = validate_python_imports(
                path,
                skip_validation=skip_validation,
                force_validation=force_validation,
            )
            for error in import_errors:
                result.add_error(error)
            for warning in import_warnings:
                result.add_warning(warning)

        # Node.js-specific validation
        if is_node_project:
            node_errors, node_warnings = validate_node_project(
                path, manifest, skip_validation=skip_validation
            )
            for error in node_errors:
                result.add_error(error)
            for warning in node_warnings:
                result.add_warning(warning)

        # Fullstack (web-app) validation - check backend Python imports and frontend Node.js
        if manifest and manifest.runtime == "fullstack":
            backend_path = path / "backend"
            if backend_path.exists():
                # Validate Python syntax in backend
                python_errors = validate_python_syntax(backend_path, exclude_dirs)
                for error in python_errors:
                    result.add_error(error)

                # Validate Python imports in backend
                import_errors, import_warnings = validate_python_imports(
                    path,
                    backend_path,
                    skip_validation=skip_validation,
                    force_validation=force_validation,
                )
                for error in import_errors:
                    result.add_error(error)
                for warning in import_warnings:
                    result.add_warning(warning)

            # Validate frontend Next.js (ESLint + TypeScript)
            frontend_path = path / "frontend"
            if frontend_path.exists() and (frontend_path / "package.json").exists():
                node_errors, node_warnings = validate_node_project(
                    frontend_path, manifest, skip_validation=skip_validation
                )
                for error in node_errors:
                    result.add_error(error)
                for warning in node_warnings:
                    result.add_warning(warning)

    return result.is_valid, result.errors, result.warnings


def _run_eslint_check(path: Path) -> tuple[list[str], list[str]]:
    """Run ESLint to catch lint errors that will fail production build.

    Next.js production builds (`next build`) fail on ESLint errors, but
    development mode (`next dev`) only shows warnings. This catches those
    errors locally before a 4+ minute remote build fails.

    Args:
        path: Path to project directory

    Returns:
        Tuple of (errors, warnings)
    """
    errors = []
    warnings = []

    # Check if ESLint is available in the project
    eslint_bin = path / "node_modules" / ".bin" / "eslint"
    if not eslint_bin.exists():
        # ESLint not installed - can't validate, just warn
        warnings.append(
            "ESLint not found in node_modules - skipping lint validation. "
            "Run 'npm install' to enable lint checks."
        )
        return errors, warnings

    console.print("  Checking ESLint...")

    try:
        # Check if project uses legacy .eslintrc config (ESLint 8 style)
        # If so, tell ESLint 9+ to use legacy config format
        eslint_env = os.environ.copy()
        has_legacy_config = any(
            (path / f).exists()
            for f in [
                ".eslintrc",
                ".eslintrc.js",
                ".eslintrc.json",
                ".eslintrc.yaml",
                ".eslintrc.yml",
            ]
        )
        if has_legacy_config:
            # ESLint 9+ requires this flag to use legacy .eslintrc configs
            eslint_env["ESLINT_USE_FLAT_CONFIG"] = "false"

        # Run ESLint with compact format for easy parsing
        # --max-warnings 0 makes any warning an error (matches Next.js production behavior)
        result = subprocess.run(
            [
                "npx",
                "eslint",
                ".",
                "--max-warnings",
                "0",
                "--format",
                "stylish",
                "--no-error-on-unmatched-pattern",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(path),
            env=eslint_env,
        )

        if result.returncode != 0:
            # ESLint found errors
            output = result.stdout.strip() or result.stderr.strip()

            # Format the error message nicely
            if output:
                # Truncate very long output but keep it useful
                if len(output) > 2000:
                    output = output[:2000] + "\n... (truncated)"

                errors.append(
                    f"ESLint errors (will fail production build):\n{output}\n\n"
                    "Fix these errors or add eslint-disable comments if intentional."
                )
            else:
                errors.append(
                    "ESLint failed with no output. Run 'npx eslint .' locally for details."
                )
        else:
            console.print("  [green]✓[/green] ESLint passed")

    except subprocess.TimeoutExpired:
        warnings.append("ESLint check timed out after 60s - skipping")
    except FileNotFoundError:
        warnings.append("npx not found - skipping ESLint validation")
    except Exception as e:
        warnings.append(f"ESLint check failed: {e}")

    return errors, warnings


def _run_typescript_check(path: Path) -> tuple[list[str], list[str]]:
    """Run TypeScript type check without emitting files.

    Catches type errors that will fail the production build.

    Args:
        path: Path to project directory

    Returns:
        Tuple of (errors, warnings)
    """
    errors = []
    warnings = []

    # Check if TypeScript is available
    tsc_bin = path / "node_modules" / ".bin" / "tsc"
    if not tsc_bin.exists():
        # TypeScript not installed - project might be JS-only
        return errors, warnings

    console.print("  Checking TypeScript...")

    try:
        # Run tsc --noEmit to check types without generating files
        result = subprocess.run(
            ["npx", "tsc", "--noEmit"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(path),
        )

        if result.returncode != 0:
            # TypeScript found errors
            output = result.stdout.strip() or result.stderr.strip()

            if output:
                # Truncate very long output
                if len(output) > 2000:
                    output = output[:2000] + "\n... (truncated)"

                errors.append(
                    f"TypeScript errors (will fail production build):\n{output}\n\n"
                    "Fix these type errors before deploying."
                )
            else:
                errors.append(
                    "TypeScript compilation failed with no output. "
                    "Run 'npx tsc --noEmit' locally for details."
                )
        else:
            console.print("  [green]✓[/green] TypeScript passed")

    except subprocess.TimeoutExpired:
        warnings.append("TypeScript check timed out after 120s - skipping")
    except FileNotFoundError:
        warnings.append("npx not found - skipping TypeScript validation")
    except Exception as e:
        warnings.append(f"TypeScript check failed: {e}")

    return errors, warnings


def validate_node_project(
    path: Path,
    manifest: Manifest | None,
    skip_validation: bool = False,
) -> tuple[list[str], list[str]]:
    """Validate Node.js project structure and build.

    Performs:
    1. JSON validation (package.json, tsconfig.json)
    2. ESLint check (~3s) - catches lint errors that fail production builds
    3. TypeScript check (~5-10s) - catches type errors

    Args:
        path: Path to project directory
        manifest: Parsed manifest (if available)
        skip_validation: Skip ESLint/TypeScript checks (faster but riskier)

    Returns:
        Tuple of (errors, warnings)
    """
    import json

    errors = []
    warnings = []

    package_json_path = path / "package.json"
    if not package_json_path.exists():
        errors.append("Missing package.json for Node.js project")
        return errors, warnings

    try:
        package_json = json.loads(package_json_path.read_text())
    except json.JSONDecodeError as e:
        errors.append(f"Invalid package.json: {e}")
        return errors, warnings

    # Check for required scripts
    scripts = package_json.get("scripts", {})
    if "build" not in scripts:
        warnings.append("No 'build' script in package.json")

    # Check for TypeScript config if using TypeScript
    if (path / "tsconfig.json").exists():
        try:
            json.loads((path / "tsconfig.json").read_text())
        except json.JSONDecodeError as e:
            errors.append(f"Invalid tsconfig.json: {e}")

    # Static-site template specific checks
    if manifest and manifest.template == "static-site":
        # Check for Next.js config
        if not (path / "next.config.js").exists() and not (path / "next.config.mjs").exists():
            warnings.append("Missing next.config.js for Next.js project")

        # Check that it's configured for static export
        next_config_path = path / "next.config.js"
        if next_config_path.exists():
            config_content = next_config_path.read_text()
            if "output" not in config_content or "'export'" not in config_content:
                warnings.append("next.config.js should have output: 'export' for static deployment")

    # Skip deep validation if requested
    if skip_validation:
        console.print("  [yellow]⚠[/yellow] Skipping Node.js build validation (--skip-validation)")
        return errors, warnings

    # Check if node_modules exists (required for ESLint/TypeScript checks)
    if not (path / "node_modules").exists():
        warnings.append(
            "node_modules not found - skipping ESLint/TypeScript validation. "
            "Run 'npm install' for full validation."
        )
        return errors, warnings

    # Run ESLint check (~3s) - catches lint errors that fail Next.js production builds
    eslint_errors, eslint_warnings = _run_eslint_check(path)
    errors.extend(eslint_errors)
    warnings.extend(eslint_warnings)

    # Run TypeScript check (~5-10s) if tsconfig.json exists
    if (path / "tsconfig.json").exists():
        ts_errors, ts_warnings = _run_typescript_check(path)
        errors.extend(ts_errors)
        warnings.extend(ts_warnings)

    return errors, warnings


def validate_python_syntax(path: Path, exclude_dirs: set) -> list[str]:
    """Validate Python syntax for all .py files in the project.

    Args:
        path: Path to project directory
        exclude_dirs: Set of directory names to exclude

    Returns:
        List of syntax error messages
    """
    errors = []

    for py_file in path.rglob("*.py"):
        # Skip excluded directories
        if any(excluded in py_file.parts for excluded in exclude_dirs):
            continue

        try:
            source = py_file.read_text()
            ast.parse(source, filename=str(py_file))
        except SyntaxError as e:
            relative_path = py_file.relative_to(path)
            errors.append(f"Syntax error in {relative_path}:{e.lineno}: {e.msg}")

    return errors


def _parse_pyproject_dependencies(pyproject_path: Path) -> tuple[list[str], list[str]]:
    """Parse production and dev dependencies from pyproject.toml.

    Uses regex parsing to avoid external TOML dependencies.

    Args:
        pyproject_path: Path to pyproject.toml file

    Returns:
        Tuple of (production_dependencies, dev_dependencies) as lists of package names
    """
    if not pyproject_path.exists():
        return [], []

    try:
        content = pyproject_path.read_text()
    except Exception:
        return [], []

    prod_deps = []
    dev_deps = []

    # Parse [project] dependencies section
    # Find the [project] section, then look for dependencies = [...]
    project_section_match = re.search(r"\[project\](.*?)(?=\n\[|\Z)", content, re.DOTALL)
    if project_section_match:
        project_section = project_section_match.group(1)
        # Find dependencies = [...] using bracket counting to handle nested brackets
        deps_start = project_section.find("dependencies = [")
        if deps_start != -1:
            bracket_count = 0
            start_idx = deps_start + len("dependencies = [")
            i = start_idx
            while i < len(project_section):
                if project_section[i] == "[":
                    bracket_count += 1
                elif project_section[i] == "]":
                    if bracket_count == 0:
                        deps_content = project_section[start_idx:i]
                        # Extract individual dependencies (handle multi-line, quotes, etc.)
                        dep_pattern = r'["\']([^"\']+)["\']'
                        for dep_match in re.finditer(dep_pattern, deps_content):
                            dep = dep_match.group(1)
                            # Extract package name (remove version constraints)
                            # Handle formats like "fastapi>=0.100.0,<1.0" or "uvicorn[standard]>=0.20.0"
                            pkg_match = re.match(r"^([a-zA-Z0-9_-]+(?:\[[^\]]+\])?)", dep.strip())
                            if pkg_match:
                                prod_deps.append(pkg_match.group(1).lower())
                        break
                    bracket_count -= 1
                i += 1

    # Parse [project.optional-dependencies] dev section
    optional_section_match = re.search(
        r"\[project\.optional-dependencies\](.*?)(?=\n\[|\Z)", content, re.DOTALL
    )
    if optional_section_match:
        optional_section = optional_section_match.group(1)
        # Find dev = [...] using bracket counting
        dev_start = optional_section.find("dev = [")
        if dev_start == -1:
            dev_start = optional_section.find("dev=[")
        if dev_start != -1:
            bracket_count = 0
            start_idx = (
                dev_start + len("dev = [")
                if "dev = [" in optional_section[dev_start : dev_start + 10]
                else dev_start + len("dev=[")
            )
            i = start_idx
            while i < len(optional_section):
                if optional_section[i] == "[":
                    bracket_count += 1
                elif optional_section[i] == "]":
                    if bracket_count == 0:
                        dev_deps_content = optional_section[start_idx:i]
                        dep_pattern = r'["\']([^"\']+)["\']'
                        for dep_match in re.finditer(dep_pattern, dev_deps_content):
                            dep = dep_match.group(1)
                            pkg_match = re.match(r"^([a-zA-Z0-9_-]+(?:\[[^\]]+\])?)", dep.strip())
                            if pkg_match:
                                dev_deps.append(pkg_match.group(1).lower())
                        break
                    bracket_count -= 1
                i += 1

    return prod_deps, dev_deps


def _normalize_package_name(module_name: str) -> str:
    """Normalize module name to package name.

    Maps common import names to their package names (e.g., 'yaml' -> 'pyyaml').
    """
    # Common mappings
    mappings = {
        "yaml": "pyyaml",
        "dotenv": "python-dotenv",
        "pkg_resources": "setuptools",
    }

    # Check if it's a direct mapping
    if module_name.lower() in mappings:
        return mappings[module_name.lower()]

    # Return as-is (most packages have the same import and package name)
    return module_name.lower()


def _detect_lockfile_type(package_path: Path) -> str | None:
    """Detect which lockfile type is present.

    Returns:
        Lockfile type: 'uv', 'poetry', or None if no lockfile found
    """
    if (package_path / "uv.lock").exists():
        return "uv"
    elif (package_path / "poetry.lock").exists():
        return "poetry"
    return None


def _run_validation_with_uv_sync(
    package_path: Path,
    main_module: str,
) -> tuple[list[str], list[str]]:
    """Run validation using uv sync (fastest, for projects with uv.lock).

    Args:
        package_path: Path to Python package
        main_module: Module to test import (e.g., "app.main")

    Returns:
        Tuple of (errors, warnings)
    """
    errors = []
    warnings = []

    with tempfile.TemporaryDirectory() as temp_dir:
        venv_path = Path(temp_dir) / ".venv"

        try:
            # uv sync creates venv and installs deps in one fast step
            result = subprocess.run(
                ["uv", "sync", "--frozen", "--no-dev"],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(package_path),
                env={**os.environ, "UV_PROJECT_ENVIRONMENT": str(venv_path)},
            )

            if result.returncode != 0:
                errors.append(
                    f"Failed to install dependencies with uv sync.\n"
                    f"  Error: {result.stderr.strip()[:200]}"
                )
                return errors, warnings

            # Get venv python path
            venv_python = venv_path / "bin" / "python"
            if sys.platform == "win32":
                venv_python = venv_path / "Scripts" / "python.exe"

            # Test import
            result = subprocess.run(
                [
                    str(venv_python),
                    "-c",
                    f"import sys; sys.path.insert(0, '.'); import {main_module}; print('OK')",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(package_path),
            )

            if result.returncode != 0:
                errors.append(f"Import error: {result.stderr.strip()[:300]}")
            else:
                console.print("  [green]✓[/green] Python imports validated (uv sync)")

        except subprocess.TimeoutExpired:
            warnings.append("Import validation timed out")
        except Exception as e:
            warnings.append(f"Import validation failed: {e}")

    return errors, warnings


def _run_validation_with_uv_pip(
    package_path: Path,
    main_module: str,
) -> tuple[list[str], list[str]]:
    """Run validation using uv venv + uv pip install (fast fallback).

    Args:
        package_path: Path to Python package
        main_module: Module to test import (e.g., "app.main")

    Returns:
        Tuple of (errors, warnings)
    """
    errors = []
    warnings = []

    with tempfile.TemporaryDirectory() as temp_dir:
        venv_path = Path(temp_dir) / ".venv"

        try:
            # Create venv with uv (much faster than python -m venv)
            result = subprocess.run(
                ["uv", "venv", str(venv_path)],
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                warnings.append("Could not create venv with uv")
                return errors, warnings

            # Get venv python path
            venv_python = venv_path / "bin" / "python"
            if sys.platform == "win32":
                venv_python = venv_path / "Scripts" / "python.exe"

            # Install with uv pip (much faster than pip)
            result = subprocess.run(
                ["uv", "pip", "install", "--python", str(venv_python), "."],
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(package_path),
            )

            if result.returncode != 0:
                errors.append(
                    f"Failed to install dependencies with uv pip.\n"
                    f"  Error: {result.stderr.strip()[:200]}"
                )
                return errors, warnings

            # Test import
            result = subprocess.run(
                [
                    str(venv_python),
                    "-c",
                    f"import sys; sys.path.insert(0, '.'); import {main_module}; print('OK')",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(package_path),
            )

            if result.returncode != 0:
                errors.append(f"Import error: {result.stderr.strip()[:300]}")
            else:
                console.print("  [green]✓[/green] Python imports validated (uv)")

        except subprocess.TimeoutExpired:
            warnings.append("Import validation timed out")
        except Exception as e:
            warnings.append(f"Import validation failed: {e}")

    return errors, warnings


def _run_validation_with_pip(
    package_path: Path,
    pyproject: Path,
    main_module: str,
) -> tuple[list[str], list[str]]:
    """Run validation using pip (slow fallback when uv not available).

    Args:
        package_path: Path to Python package
        pyproject: Path to pyproject.toml
        main_module: Module to test import (e.g., "app.main")

    Returns:
        Tuple of (errors, warnings)
    """
    errors = []
    warnings = []

    with tempfile.TemporaryDirectory() as temp_dir:
        venv_path = Path(temp_dir) / "venv"

        try:
            # Create venv
            result = subprocess.run(
                [sys.executable, "-m", "venv", str(venv_path)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                warnings.append("Could not create venv for import validation")
                return errors, warnings

            # Get venv python path
            if sys.platform == "win32":
                venv_python = venv_path / "Scripts" / "python.exe"
            else:
                venv_python = venv_path / "bin" / "python"

            # Install production dependencies only (not dev)
            result = subprocess.run(
                [
                    str(venv_python),
                    "-m",
                    "pip",
                    "install",
                    "--no-cache-dir",
                    ".",
                ],
                capture_output=True,
                text=True,
                timeout=180,
                cwd=str(package_path),
            )
            if result.returncode != 0:
                stderr = result.stderr
                if "No module named" in stderr:
                    errors.append(f"Failed to install package: {stderr.strip()}")
                else:
                    errors.append(
                        f"Failed to install Python package. Check pyproject.toml.\n"
                        f"  Error: {stderr.strip()[:200]}"
                    )
                return errors, warnings

            # Verify that dependencies were actually installed
            check_result = subprocess.run(
                [str(venv_python), "-m", "pip", "list"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            installed_packages = check_result.stdout.lower() if check_result.returncode == 0 else ""

            # Try to import the main module
            result = subprocess.run(
                [
                    str(venv_python),
                    "-c",
                    f"import sys; sys.path.insert(0, '.'); import {main_module}; print('OK')",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(package_path),
            )

            if result.returncode != 0:
                stderr = result.stderr
                # Parse common import errors
                if "ModuleNotFoundError: No module named" in stderr:
                    match = re.search(r"No module named '([^']+)'", stderr)
                    if match:
                        missing_module = match.group(1)
                        prod_deps, dev_deps = _parse_pyproject_dependencies(pyproject)
                        normalized_module = _normalize_package_name(missing_module)

                        def normalize_dep(dep: str) -> str:
                            base = dep.split("[")[0]
                            return _normalize_package_name(base)

                        prod_deps_normalized = [normalize_dep(dep) for dep in prod_deps]
                        dev_deps_normalized = [normalize_dep(dep) for dep in dev_deps]

                        is_in_prod = normalized_module in prod_deps_normalized
                        is_in_dev = normalized_module in dev_deps_normalized

                        reverse_mappings = {
                            "pyyaml": "yaml",
                            "python-dotenv": "dotenv",
                            "setuptools": "pkg_resources",
                        }
                        for pkg_name, import_name in reverse_mappings.items():
                            if (
                                normalized_module == import_name
                                and pkg_name in prod_deps_normalized
                            ):
                                is_in_prod = True
                            if normalized_module == import_name and pkg_name in dev_deps_normalized:
                                is_in_dev = True

                        if is_in_prod:
                            pkg_installed = normalized_module in installed_packages
                            if pkg_installed:
                                errors.append(
                                    f"Dependency '{missing_module}' is installed but cannot be imported.\n"
                                    f"  This may be a Python path issue. Verify your project structure.\n"
                                    f"  Error: {stderr.strip()[:300]}"
                                )
                            else:
                                errors.append(
                                    f"Dependency '{missing_module}' is listed in pyproject.toml but pip failed to install it.\n"
                                    f"  Found in dependencies: {', '.join(prod_deps[:5])}{'...' if len(prod_deps) > 5 else ''}\n"
                                    f"  Try running: pip install {missing_module}\n"
                                    f"  Error: {stderr.strip()[:300]}"
                                )
                        elif is_in_dev:
                            errors.append(
                                f"Missing dependency: '{missing_module}' is imported but only listed in "
                                f"[project.optional-dependencies] dev.\n"
                                f"  Add it to [project] dependencies (not dev) for production use."
                            )
                        else:
                            deps_preview = ", ".join(prod_deps[:5]) + (
                                "..." if len(prod_deps) > 5 else ""
                            )
                            if not prod_deps:
                                deps_preview = "(none found)"
                            errors.append(
                                f"Missing dependency: '{missing_module}' is imported but not in "
                                f"pyproject.toml dependencies.\n"
                                f"  Found dependencies: {deps_preview}\n"
                                f"  Add '{missing_module}' to [project] dependencies (not [project.optional-dependencies] dev)"
                            )
                    else:
                        errors.append(f"Import error: {stderr.strip()[:300]}")
                elif "ImportError" in stderr:
                    errors.append(f"Import error in {main_module}: {stderr.strip()[:300]}")
                else:
                    errors.append(f"Failed to import {main_module}: {stderr.strip()[:300]}")
            else:
                console.print("  [green]✓[/green] Python imports validated (pip)")

        except subprocess.TimeoutExpired:
            warnings.append("Import validation timed out")
        except Exception as e:
            warnings.append(f"Import validation failed: {e}")

    return errors, warnings


def validate_python_imports(
    project_path: Path,
    backend_path: Path | None = None,
    skip_validation: bool = False,
    force_validation: bool = False,
) -> tuple[list[str], list[str]]:
    """Validate Python imports by testing in a clean venv with production deps only.

    This is **developer experience** validation - it catches obvious errors early.
    It is NOT a correctness proof (local imports can pass while container build fails
    due to different OS/Python version/missing system packages).

    Optimization strategy:
    1. Skip entirely if --skip-validation
    2. Check cache if deps+env unchanged (unless --force-validation)
    3. Use fastest available tool: uv sync > uv pip > pip
    4. Cache success only (never cache failures)

    Args:
        project_path: Path to project root
        backend_path: Path to backend directory (for fullstack apps)
        skip_validation: Skip validation entirely (faster but riskier)
        force_validation: Force re-validation even if cached

    Returns:
        Tuple of (errors, warnings)
    """
    errors = []
    warnings = []

    # 1. Skip if explicitly requested
    if skip_validation:
        console.print("  [yellow]⚠[/yellow] Skipping Python import validation (--skip-validation)")
        return errors, warnings

    # Determine the Python package path
    if backend_path and backend_path.exists():
        package_path = backend_path
        pyproject = backend_path / "pyproject.toml"
    elif (project_path / "backend").exists():
        package_path = project_path / "backend"
        pyproject = package_path / "pyproject.toml"
    elif (project_path / "pyproject.toml").exists():
        package_path = project_path
        pyproject = project_path / "pyproject.toml"
    else:
        # No Python project to validate
        return errors, warnings

    if not pyproject.exists():
        return errors, warnings

    # Find the main module to import
    app_dir = package_path / "app"
    if not app_dir.exists():
        warnings.append("No 'app' directory found - skipping import validation")
        return errors, warnings

    main_module = "app.main"
    if not (app_dir / "main.py").exists():
        warnings.append("No 'app/main.py' found - skipping import validation")
        return errors, warnings

    # 2. Check cache (unless --force-validation)
    cache_key = _compute_validation_cache_key(package_path, pyproject)

    if not force_validation and _check_validation_cache(cache_key):
        console.print("  [dim]Deps unchanged - skipping import validation (cached)[/dim]")
        return errors, warnings

    console.print("  Checking Python imports with production dependencies...")

    # 3. Detect lockfile type and choose best validation method
    lockfile_type = _detect_lockfile_type(package_path)
    has_uv = shutil.which("uv") is not None

    if lockfile_type == "uv" and has_uv:
        # Best case: uv.lock exists and uv is available
        # Use uv sync --frozen for fastest, most reproducible validation
        errors, warnings = _run_validation_with_uv_sync(package_path, main_module)
    elif has_uv:
        # uv is available but no uv.lock - use uv venv + uv pip install
        errors, warnings = _run_validation_with_uv_pip(package_path, main_module)
    else:
        # Fallback: use pip (slower)
        errors, warnings = _run_validation_with_pip(package_path, pyproject, main_module)

    # 4. Cache success only (never cache failures)
    if not errors:
        _write_validation_cache(cache_key)

    return errors, warnings


def validate_health_config(manifest: Manifest) -> tuple[list[str], list[str]]:
    """Validate health endpoint configuration in manifest.

    Checks that health_path is properly configured. The actual health
    endpoint behavior is checked at runtime, not statically.

    Args:
        manifest: Parsed manifest

    Returns:
        Tuple of (errors, warnings)
    """
    errors = []
    warnings = []

    # Check health_path exists and is valid
    if not manifest.health_path:
        errors.append("Manifest missing health_path. Add 'health_path: /health' to runtm.yaml")
    elif not manifest.health_path.startswith("/"):
        errors.append(f"health_path must start with /. Got: {manifest.health_path}")

    # Warn if non-standard health path
    if manifest.health_path and manifest.health_path != "/health":
        warnings.append(
            f"Non-standard health_path: {manifest.health_path}. "
            "Consider using /health for consistency."
        )

    return errors, warnings


def validate_dockerfile_cache_hygiene(dockerfile_path: Path) -> list[str]:
    """Validate Dockerfile structure for optimal layer caching.

    Checks that dependency manifests are copied and dependencies installed
    BEFORE copying the full source code. This ensures that code-only changes
    only invalidate the final layers, not the expensive dependency install layers.

    Correct pattern:
        COPY package.json .
        RUN npm install
        COPY . .

    Anti-pattern (busts cache on every code change):
        COPY . .
        RUN npm install

    Args:
        dockerfile_path: Path to Dockerfile

    Returns:
        List of warning messages (not errors - user may have valid reasons)
    """
    warnings = []

    if not dockerfile_path.exists():
        return warnings

    try:
        content = dockerfile_path.read_text()
    except Exception:
        return warnings

    # Remove comments to avoid false positives
    lines = []
    for line in content.split("\n"):
        # Strip comments but preserve the line structure
        comment_pos = line.find("#")
        if comment_pos != -1:
            line = line[:comment_pos]
        lines.append(line)
    content_no_comments = "\n".join(lines)

    # Find positions of key patterns
    # Look for "COPY . ." or "COPY . ./" which copies everything
    copy_all_pattern = re.compile(r"COPY\s+\.\s+\./?", re.IGNORECASE)
    copy_all_match = copy_all_pattern.search(content_no_comments)

    if not copy_all_match:
        # No "COPY . ." found - either good structure or no source copy at all
        return warnings

    copy_all_pos = copy_all_match.start()

    # Find dependency install commands
    install_patterns = [
        (r"RUN\s+.*npm\s+(install|ci)", "npm"),
        (r"RUN\s+.*bun\s+install", "bun"),
        (r"RUN\s+.*pip\s+install", "pip"),
        (r"RUN\s+.*uv\s+(sync|pip)", "uv"),
        (r"RUN\s+.*yarn\s+(install)?", "yarn"),
        (r"RUN\s+.*pnpm\s+install", "pnpm"),
    ]

    # Find the first install command position
    first_install_pos = None
    install_tool = None

    for pattern, tool in install_patterns:
        match = re.search(pattern, content_no_comments, re.IGNORECASE)
        if match and (first_install_pos is None or match.start() < first_install_pos):
            first_install_pos = match.start()
            install_tool = tool

    if first_install_pos is None:
        # No install command found - nothing to warn about
        return warnings

    # Check if COPY . . comes BEFORE the first install command
    if copy_all_pos < first_install_pos:
        warnings.append(
            f"Dockerfile: 'COPY . .' appears before '{install_tool} install'. "
            "This will bust the layer cache on every code change, causing slow rebuilds. "
            "For faster deploys, copy lockfiles first, install deps, then copy source:\n"
            "    COPY package*.json ./\n"
            "    RUN npm install\n"
            "    COPY . ."
        )

    return warnings


def validate_command(
    path: Path = typer.Argument(
        Path("."),
        help="Path to project directory",
        exists=True,
        file_okay=False,
        dir_okay=True,
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON for AI agents",
    ),
) -> None:
    """Validate project before deployment.

    Checks:
    - runtm.yaml exists and is valid
    - Dockerfile exists
    - Artifact size is within limits
    - No env/secrets in manifest (not supported in V0)

    Examples:
        runtm validate                    # Human-readable output
        runtm validate --json             # JSON for AI agents
        runtm validate ./my-project       # Validate specific directory
    """
    import json

    is_valid, errors, warnings = validate_project(path)

    # JSON output for programmatic consumption
    if json_output:
        result = {
            "valid": is_valid,
            "errors": errors,
            "warnings": warnings,
        }
        print(json.dumps(result))
        raise typer.Exit(0 if is_valid else 1)

    # Human-readable output
    console.print(f"Validating project: {path.absolute()}")
    console.print()

    # Show warnings
    for warning in warnings:
        console.print(f"[yellow]⚠[/yellow] {warning}")

    # Show errors
    for error in errors:
        console.print(f"[red]✗[/red] {error}")

    console.print()

    if is_valid:
        console.print("[green]✓[/green] Project is valid and ready to deploy")
    else:
        console.print("[red]✗[/red] Validation failed. Fix the errors above and try again.")
        raise typer.Exit(1)
