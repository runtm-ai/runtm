"""Deploy command - deploy project to live URL."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner

from runtm_shared.errors import RuntmError
from runtm_shared.lockfiles import check_lockfile, fix_lockfile
from runtm_shared.manifest import Manifest
from runtm_shared.types import MACHINE_TIER_SPECS, MachineTier

from ..api_client import APIClient, compute_src_hash, create_artifact_zip
from ..config import get_token
from .validate import validate_project

console = Console()


def _emit_json_event(event: dict[str, Any]) -> None:
    """Emit a JSON event to stdout (NDJSON format).

    Each event is a single line of JSON, flushed immediately.
    """
    print(json.dumps(event), flush=True)


def deploy_command(
    path: Path = Path("."),
    wait: bool = True,
    timeout: int = 500,
    new: bool = False,
    tier: str | None = None,
    yes: bool = False,
    config_only: bool = False,
    skip_validation: bool = False,
    force_validation: bool = False,
    json_output: bool = False,
) -> None:
    """Deploy a project to a live URL.

    By default, if a deployment with the same name already exists,
    this will redeploy (update) the existing deployment. The URL
    stays the same across versions.

    Use --new to force creation of a completely new deployment.
    Use --yes to auto-fix lockfile issues without prompting.
    Use --config-only to skip Docker build and reuse the previous image.
    (Requires unchanged source code - only for env var or tier changes.)
    Use --skip-validation to skip Python import validation (faster but riskier).
    Use --force-validation to ignore validation cache and re-run checks.
    Use --json for NDJSON output for AI agents (one JSON object per line).

    Machine tiers (all use auto-stop for cost savings):
      - starter: 1 shared CPU, 256MB RAM (~$2/month, much less with auto-stop)
      - standard: 1 shared CPU, 512MB RAM (~$5/month, much less with auto-stop)
      - performance: 2 shared CPUs, 1GB RAM (~$10/month, much less with auto-stop)

    Examples:
        runtm deploy                    # Deploy current directory
        runtm deploy --json             # NDJSON output for AI agents
        runtm deploy --yes              # Auto-fix lockfile issues
    """
    from runtm_cli.telemetry import (
        command_span,
        emit_auth_failed,
        emit_deploy_completed,
        emit_deploy_failed,
        emit_deploy_started,
        emit_deploy_validation_failed,
        phase_span,
    )

    deploy_start_time = time.time()
    artifact_size: float | None = None

    # Helper for conditional output
    def log(msg: str) -> None:
        if not json_output:
            console.print(msg)

    def log_warning(msg: str) -> None:
        if not json_output:
            console.print(f"[yellow]⚠[/yellow] {msg}")

    def log_success(msg: str) -> None:
        if not json_output:
            console.print(f"[green]✓[/green] {msg}")

    with command_span("deploy", {"runtm.tier": tier or "default"}):
        # Validate tier if provided
        if tier is not None:
            try:
                tier_enum = MachineTier(tier)
                tier_spec = MACHINE_TIER_SPECS[tier_enum]
            except ValueError:
                valid_tiers = ", ".join(t.value for t in MachineTier)
                if json_output:
                    _emit_json_event(
                        {
                            "phase": "init",
                            "status": "failed",
                            "error": f"Invalid tier: {tier}. Valid tiers: {valid_tiers}",
                        }
                    )
                else:
                    console.print(f"[red]✗[/red] Invalid tier: {tier}")
                    console.print(f"    Valid tiers: {valid_tiers}")
                raise typer.Exit(1)

        # Check auth - verify token exists AND is valid before expensive work
        token = get_token()
        if not token:
            emit_auth_failed("missing_token")
            if json_output:
                _emit_json_event(
                    {
                        "phase": "auth",
                        "status": "failed",
                        "error": "Not authenticated",
                        "hint": "Run `runtm login` or set RUNTM_API_KEY",
                    }
                )
            else:
                console.print("[red]✗[/red] Not authenticated. Run `runtm login` first.")
                console.print()
                console.print("Or set RUNTM_API_KEY environment variable.")
            raise typer.Exit(1)

        # Pre-flight auth check: verify token is valid before doing validation/artifact work
        with phase_span("auth_verify"):
            client = APIClient()
            if not client.check_auth():
                emit_auth_failed("invalid_token")
                if json_output:
                    _emit_json_event(
                        {
                            "phase": "auth",
                            "status": "failed",
                            "error": "Authentication failed. Token may be invalid or expired.",
                            "hint": "Run `runtm login` or check RUNTM_API_KEY",
                        }
                    )
                else:
                    console.print(
                        "[red]✗[/red] Authentication failed. Your token may be invalid or expired."
                    )
                    console.print()
                    console.print("Try: runtm login")
                    console.print("Or check your RUNTM_API_KEY environment variable.")
                raise typer.Exit(1)

        # Validate first
        with phase_span("validate"):
            if json_output:
                _emit_json_event({"phase": "validate", "status": "running"})
            else:
                console.print("Validating project...")

            is_valid, errors, warnings = validate_project(
                path,
                skip_validation=skip_validation,
                force_validation=force_validation,
            )

            if not is_valid:
                emit_deploy_validation_failed(len(errors), len(warnings))
                if json_output:
                    _emit_json_event(
                        {
                            "phase": "validate",
                            "status": "failed",
                            "errors": errors,
                            "warnings": warnings,
                        }
                    )
                else:
                    for warning in warnings:
                        console.print(f"[yellow]⚠[/yellow] {warning}")
                    for error in errors:
                        console.print(f"[red]✗[/red] {error}")
                    console.print()
                    console.print("Fix validation errors and try again.")
                raise typer.Exit(1)

            if json_output:
                _emit_json_event(
                    {
                        "phase": "validate",
                        "status": "passed",
                        "warnings": warnings,
                    }
                )
            else:
                for warning in warnings:
                    console.print(f"[yellow]⚠[/yellow] {warning}")
                console.print("[green]✓[/green] Project validated")

        # Check lockfile (prod must be reproducible)
        # Skip for docker template - user brings their own Dockerfile
        with phase_span("lockfile_check"):
            try:
                manifest = Manifest.from_file(path / "runtm.yaml")
                
                # Skip lockfile check for docker template (no runtime specified)
                if manifest.template == "docker" or not manifest.runtime:
                    log_success("Lockfile check skipped (docker template)")
                else:
                    lockfile_status = check_lockfile(path, manifest.runtime)

                    if lockfile_status.needs_fix:
                        if yes:
                            action = "Creating" if not lockfile_status.exists else "Fixing"
                            log(
                                f"[yellow]{action} lockfile via {lockfile_status.install_cmd}...[/yellow]"
                            )
                            if fix_lockfile(path, lockfile_status):
                                log_success("Lockfile fixed")
                            else:
                                if json_output:
                                    _emit_json_event(
                                        {
                                            "phase": "lockfile",
                                            "status": "failed",
                                            "error": "Failed to fix lockfile",
                                            "hint": f"Run manually: {lockfile_status.install_cmd}",
                                        }
                                    )
                                else:
                                    console.print("[red]✗[/red] Failed to fix lockfile")
                                    console.print(f"    Run manually: {lockfile_status.install_cmd}")
                                raise typer.Exit(1)
                        else:
                            issue = "missing" if not lockfile_status.exists else "out of sync"
                            if json_output:
                                _emit_json_event(
                                    {
                                        "phase": "lockfile",
                                        "status": "failed",
                                        "error": f"Lockfile {issue}",
                                        "hint": f"Run: {lockfile_status.install_cmd} or deploy with --yes",
                                    }
                                )
                            else:
                                console.print(f"[red]✗[/red] Lockfile {issue}")
                                console.print(f"    Run: [bold]{lockfile_status.install_cmd}[/bold]")
                                console.print("    Or deploy with: [bold]runtm deploy --yes[/bold]")
                            raise typer.Exit(1)
                    else:
                        log_success("Lockfile in sync")
            except typer.Exit:
                raise
            except Exception as e:
                # Don't block deploy on lockfile check errors, just warn
                log_warning(f"Could not check lockfile: {e}")

        # Validate env vars (if env_schema is defined)
        resolved_secrets: dict = {}
        with phase_span("env_validation"):
            if manifest.env_schema:
                from runtm_cli.commands.secrets import resolve_env_vars

                resolved, missing_required, env_warnings = resolve_env_vars(path, manifest)

                for warning in env_warnings:
                    log_warning(warning)

                if missing_required:
                    emit_deploy_validation_failed(len(missing_required), 0)
                    if json_output:
                        _emit_json_event(
                            {
                                "phase": "env",
                                "status": "failed",
                                "error": "Missing required environment variables",
                                "missing": missing_required,
                            }
                        )
                    else:
                        console.print("[red]✗[/red] Missing required environment variables:")
                        for name in missing_required:
                            console.print(f"    - {name}")
                        console.print()
                        console.print("Set them with:")
                        for name in missing_required:
                            console.print(f"  runtm secrets set {name}=<value>")
                    raise typer.Exit(1)

                # Extract secrets for injection (values marked as secret: true)
                secret_names = {ev.name for ev in manifest.get_secret_env_vars()}
                resolved_secrets = {k: v for k, v in resolved.items() if k in secret_names}

                if resolved:
                    log_success(f"Environment variables resolved ({len(resolved)} vars)")
                    if resolved_secrets and not json_output:
                        console.print(
                            f"    [dim]{len(resolved_secrets)} secrets will be injected[/dim]"
                        )

        # Check for discovery metadata (optional, just warn)
        discovery_path = path / "runtm.discovery.yaml"
        if not discovery_path.exists():
            log_warning(
                "No runtm.discovery.yaml found. Consider adding app metadata for discoverability."
            )
        else:
            # Check if discovery file has unfilled TODO placeholders
            try:
                discovery_content = discovery_path.read_text()
                if "# TODO:" in discovery_content or "TODO:" in discovery_content:
                    log_warning(
                        "runtm.discovery.yaml has unfilled TODO placeholders. "
                        "Fill them in for better app discoverability."
                    )
            except Exception:
                pass  # Don't block on read errors

        # Create artifact
        with phase_span("artifact_create"):
            log("Creating artifact...")
            manifest_path = path / "runtm.yaml"

            # If tier is specified via CLI, update the manifest temporarily
            original_manifest_content = None
            if tier is not None:
                try:
                    import yaml

                    original_manifest_content = manifest_path.read_text()
                    manifest_data = yaml.safe_load(original_manifest_content)
                    manifest_data["tier"] = tier
                    manifest_path.write_text(
                        yaml.safe_dump(manifest_data, default_flow_style=False, sort_keys=False)
                    )
                    if not json_output:
                        console.print(f"[dim]Using tier: {tier_spec.description}[/dim]")
                except Exception as e:
                    log_warning(f"Could not update manifest tier: {e}")

            try:
                artifact_path = create_artifact_zip(path)
                artifact_size = artifact_path.stat().st_size / (1024 * 1024)
                log_success(f"Artifact created ({artifact_size:.1f} MB)")
            except Exception as e:
                # Restore original manifest if we modified it
                if original_manifest_content is not None:
                    manifest_path.write_text(original_manifest_content)
                emit_deploy_failed("artifact_error")
                if json_output:
                    _emit_json_event(
                        {
                            "phase": "artifact",
                            "status": "failed",
                            "error": f"Failed to create artifact: {e}",
                        }
                    )
                else:
                    console.print(f"[red]✗[/red] Failed to create artifact: {e}")
                raise typer.Exit(1)
            finally:
                # Restore original manifest after creating artifact
                if original_manifest_content is not None:
                    manifest_path.write_text(original_manifest_content)

        # Deploy
        client = APIClient()

        # Compute source hash for tracking and config-only validation
        src_hash: str | None = None
        with phase_span("src_hash"):
            try:
                src_hash = compute_src_hash(path)
                if not json_output:
                    console.print(f"[dim]Source hash: {src_hash}[/dim]")
            except Exception as e:
                log_warning(f"Could not compute source hash: {e}")

        # Validate --config-only flag
        if config_only:
            with phase_span("config_only_validation"):
                if new:
                    if json_output:
                        _emit_json_event(
                            {
                                "phase": "config_only",
                                "status": "failed",
                                "error": "--config-only cannot be used with --new",
                            }
                        )
                    else:
                        console.print("[red]✗[/red] --config-only cannot be used with --new")
                        console.print(
                            "    Config-only deploys require a previous deployment to reuse."
                        )
                    raise typer.Exit(1)

                # Get previous deployment to check src_hash
                try:
                    previous = client.get_latest_deployment_for_name(manifest.name)
                except Exception:
                    previous = None

                if not previous:
                    if json_output:
                        _emit_json_event(
                            {
                                "phase": "config_only",
                                "status": "failed",
                                "error": "--config-only requires a previous deployment",
                            }
                        )
                    else:
                        console.print("[red]✗[/red] --config-only requires a previous deployment")
                        console.print("    No existing deployment found for this project name.")
                        console.print(
                            "    Use `runtm deploy` (without --config-only) for first deploy."
                        )
                    raise typer.Exit(1)

                # Check if source has changed
                if previous.src_hash and src_hash and previous.src_hash != src_hash:
                    if json_output:
                        _emit_json_event(
                            {
                                "phase": "config_only",
                                "status": "failed",
                                "error": "Source code has changed since last deploy",
                                "previous_hash": previous.src_hash,
                                "current_hash": src_hash,
                            }
                        )
                    else:
                        console.print("[red]✗[/red] Source code has changed since last deploy")
                        console.print(f"    Previous: {previous.src_hash}")
                        console.print(f"    Current:  {src_hash}")
                        console.print()
                        console.print("--config-only requires unchanged source code.")
                        console.print(
                            "Use `runtm deploy` (without --config-only) for a full rebuild."
                        )
                    raise typer.Exit(1)

                if not previous.src_hash:
                    log_warning(
                        "Previous deployment has no src_hash. Cannot validate source unchanged. Proceeding anyway."
                    )

                log_success("Config-only deploy - skipping Docker build")

        with phase_span("api_call"):
            # Emit deploy started event
            emit_deploy_started(
                is_redeploy=False,
                tier=tier,
                artifact_size_mb=artifact_size,
                template=manifest.template,
            )

            try:
                if config_only:
                    log("Creating config-only deployment (reusing previous image)...")
                elif new:
                    log("Creating new deployment...")
                else:
                    log("Creating deployment (will redeploy if name exists)...")

                deployment = client.create_deployment(
                    manifest_path,
                    artifact_path,
                    force_new=new,
                    tier=tier,
                    secrets=resolved_secrets,
                    src_hash=src_hash,
                    config_only=config_only,
                )

                if json_output:
                    _emit_json_event(
                        {
                            "phase": "deploy",
                            "status": "running",
                            "deployment_id": deployment.deployment_id,
                            "version": deployment.version,
                        }
                    )
                else:
                    if deployment.version > 1:
                        console.print(
                            f"[green]✓[/green] Redeployment created: {deployment.deployment_id} "
                            f"(v{deployment.version})"
                        )
                        console.print(f"    Updating from: {deployment.previous_deployment_id}")
                    else:
                        console.print(
                            f"[green]✓[/green] Deployment created: {deployment.deployment_id}"
                        )
            except RuntmError as e:
                emit_deploy_failed("api_error")
                if json_output:
                    _emit_json_event(
                        {
                            "phase": "deploy",
                            "status": "failed",
                            "error": e.message,
                            "hint": e.recovery_hint,
                        }
                    )
                else:
                    console.print(f"[red]✗[/red] {e.message}")
                    if e.recovery_hint:
                        console.print(f"    {e.recovery_hint}")
                raise typer.Exit(1)

        if not wait:
            if json_output:
                _emit_json_event(
                    {
                        "phase": "deploy",
                        "status": "queued",
                        "deployment_id": deployment.deployment_id,
                        "state": deployment.state,
                    }
                )
            else:
                console.print()
                console.print(f"Deployment ID: {deployment.deployment_id}")
                console.print(f"Status: {deployment.state}")
                console.print()
                console.print("Check status with:")
                console.print(f"  runtm status {deployment.deployment_id}")
            return

        # Wait for completion
        with phase_span("poll_status"):
            poll_start_time = time.time()
            last_state = deployment.state

            # For JSON output, don't use Live display
            if json_output:
                while True:
                    elapsed = time.time() - poll_start_time
                    if elapsed > timeout:
                        emit_deploy_failed("timeout", state_reached=deployment.state)
                        _emit_json_event(
                            {
                                "phase": "deploy",
                                "status": "failed",
                                "error": f"Deployment timed out after {timeout}s",
                                "deployment_id": deployment.deployment_id,
                                "state": deployment.state,
                            }
                        )
                        raise typer.Exit(1)

                    # Check status
                    try:
                        deployment = client.get_deployment(deployment.deployment_id)
                    except RuntmError:
                        pass  # Keep polling

                    if deployment.state != last_state:
                        last_state = deployment.state
                        # Emit state change event
                        _emit_json_event(
                            {
                                "phase": "deploy",
                                "status": "running",
                                "deployment_id": deployment.deployment_id,
                                "state": deployment.state,
                            }
                        )

                    # Check terminal states
                    if deployment.state == "ready":
                        break
                    elif deployment.state == "failed":
                        emit_deploy_failed("deployment_failed", state_reached="failed")
                        _emit_json_event(
                            {
                                "phase": "deploy",
                                "status": "failed",
                                "deployment_id": deployment.deployment_id,
                                "error": deployment.error_message or "Deployment failed",
                            }
                        )
                        raise typer.Exit(1)

                    time.sleep(2)
            else:
                with Live(console=console, refresh_per_second=4) as live:
                    while True:
                        elapsed = time.time() - poll_start_time
                        if elapsed > timeout:
                            live.stop()
                            emit_deploy_failed("timeout", state_reached=deployment.state)
                            console.print(f"[red]✗[/red] Deployment timed out after {timeout}s")
                            console.print()
                            console.print("Check status with:")
                            console.print(f"  runtm status {deployment.deployment_id}")
                            raise typer.Exit(1)

                        # Update display
                        spinner = Spinner("dots", text=f" Deploying... ({deployment.state})")
                        live.update(spinner)

                        # Check status
                        try:
                            deployment = client.get_deployment(deployment.deployment_id)
                        except RuntmError:
                            pass  # Keep polling

                        if deployment.state != last_state:
                            last_state = deployment.state

                        # Check terminal states
                        if deployment.state == "ready":
                            live.stop()
                            break
                        elif deployment.state == "failed":
                            live.stop()
                            emit_deploy_failed("deployment_failed", state_reached="failed")
                            console.print("[red]✗[/red] Deployment failed")
                            if deployment.error_message:
                                console.print(f"    {deployment.error_message}")
                            console.print()
                            console.print("View logs with:")
                            console.print(f"  runtm logs {deployment.deployment_id}")
                            raise typer.Exit(1)

                        time.sleep(2)

        # Success!
        duration_ms = (time.time() - deploy_start_time) * 1000
        duration_secs = duration_ms / 1000
        emit_deploy_completed(duration_ms, version=deployment.version, template=manifest.template)

        if json_output:
            _emit_json_event(
                {
                    "phase": "deploy",
                    "status": "ready",
                    "deployment_id": deployment.deployment_id,
                    "url": deployment.url,
                    "version": deployment.version,
                    "duration_seconds": round(duration_secs, 1),
                }
            )
        else:
            console.print(f"[green]✓[/green] Deployed in {duration_secs:.1f}s")
            console.print()
            console.print(f"  URL: {deployment.url}")
            console.print(f"  ID:  {deployment.deployment_id}")
            console.print()
            console.print("Next steps:")
            console.print(f"  runtm status {deployment.deployment_id}")
            console.print(f"  runtm logs {deployment.deployment_id}")
            console.print(f"  curl {deployment.url}/health")
