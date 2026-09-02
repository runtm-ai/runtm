"""Docker build and push helpers with BuildKit and remote builder support."""

from __future__ import annotations

import os
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import docker
from docker.errors import BuildError as DockerBuildError
from docker.errors import DockerException

from runtm_shared import AppDiscovery
from runtm_shared.errors import BuildError
from runtm_shared.urls import construct_deployment_url


@dataclass
class BuildResult:
    """Result of a Docker build operation."""

    success: bool
    image_tag: str | None = None
    image_label: str | None = None  # Label for image reuse/rollbacks
    error: str | None = None
    logs: list[str] = field(default_factory=list)
    # If True, the remote builder also deployed the app (skip deploy phase)
    deployed: bool = False
    # App URL if deployed by remote builder
    url: str | None = None
    # Discovery metadata from runtm.discovery.yaml (if found)
    discovery_json: dict | None = None


class DockerBuilder:
    """Docker image builder with BuildKit and remote builder support.

    Builds and deploys container images from deployment artifacts.

    Modes:
        Remote Builder (Default, Recommended):
            - Uses flyctl deploy --buildkit to build AND deploy in one step
            - Builds on Fly's remote BuildKit infrastructure (no local Docker needed)
            - Auto-generates fly.toml with optimized health check settings
            - Returns deployed=True with URL when complete

        Local Build (Fallback):
            - Uses local Docker daemon with BuildKit
            - Builds image locally, then pushes to Fly registry
            - Requires separate machine creation step

    Features:
        - BuildKit for parallel multi-stage builds and layer caching
        - Auto-generated fly.toml with health checks and graceful shutdown
    """

    def __init__(
        self,
        registry: str = "registry.fly.io",
        log_callback: Callable[[str], None] | None = None,
        use_remote_builder: bool = False,
    ):
        """Initialize Docker builder.

        Args:
            registry: Docker registry URL
            log_callback: Optional callback for streaming logs
            use_remote_builder: Use Fly's remote builder instead of local Docker
        """
        self.registry = registry
        self.log_callback = log_callback
        self.use_remote_builder = use_remote_builder

        # Enable BuildKit by default
        os.environ["DOCKER_BUILDKIT"] = "1"

        if not use_remote_builder:
            self.client = docker.from_env()

    def _log(self, message: str, logs: list[str]) -> None:
        """Log a message.

        Args:
            message: Message to log
            logs: List to append to
        """
        logs.append(message)
        if self.log_callback:
            self.log_callback(message)

    def extract_artifact(
        self,
        artifact_path: Path,
        dest_dir: Path,
    ) -> None:
        """Extract artifact zip to destination directory.

        Args:
            artifact_path: Path to artifact.zip
            dest_dir: Destination directory

        Raises:
            BuildError: If extraction fails
        """
        try:
            with zipfile.ZipFile(artifact_path, "r") as zf:
                zf.extractall(dest_dir)
        except zipfile.BadZipFile as e:
            raise BuildError(f"Invalid artifact zip: {e}") from e
        except Exception as e:
            raise BuildError(f"Failed to extract artifact: {e}") from e

    def build_remote(
        self,
        context_path: Path,
        app_name: str,
        deployment_id: str,
        fly_api_token: str,
        timeout_seconds: int = 600,
        internal_port: int = 3000,
        health_check_path: str = "/health",
        memory_mb: int = 256,
        volumes: list | None = None,
    ) -> BuildResult:
        """Build AND deploy using Fly's remote builder (recommended).

        This is the fastest deployment path. Uses `flyctl deploy` to:
        1. Build the Docker image on Fly's remote builders
        2. Push to Fly's registry (internal, fast)
        3. Deploy to a Fly machine with health checks
        4. Wait for the app to become healthy

        Auto-generates fly.toml with:
        - Optimized health check settings (120s grace period for fullstack apps)
        - Auto-stop/auto-start for cost savings
        - Graceful shutdown (SIGTERM, 30s timeout)
        - Persistent volume mounts (if volumes provided)

        Args:
            context_path: Path to build context (extracted artifact)
            app_name: Fly app name (must already exist)
            deployment_id: Deployment ID for tagging
            fly_api_token: Fly.io API token
            timeout_seconds: Build timeout
            internal_port: Internal port the app listens on (default 3000)
            health_check_path: Health check endpoint path (default /health)
            memory_mb: Memory in MB (determines VM size)
            volumes: Persistent volume configs (VolumeConfig objects with name, path, size_gb)

        Returns:
            BuildResult with deployed=True and url if successful
        """
        logs: list[str] = []
        clean_id = deployment_id[:12].replace("_", "-")
        image_tag = f"{self.registry}/{app_name}:{clean_id}"

        self._log(f"Using Fly remote builder for: {app_name}", logs)
        self._log(f"Context: {context_path}", logs)

        # Check for Dockerfile
        dockerfile_path = context_path / "Dockerfile"
        if not dockerfile_path.exists():
            return BuildResult(
                success=False,
                error="Dockerfile not found in artifact",
                logs=logs,
            )

        # Generate fly.toml with full configuration (required by flyctl deploy)
        fly_toml_path = context_path / "fly.toml"
        self._log(f"Generating fly.toml for deployment (memory: {memory_mb}MB)...", logs)

        # Map memory to Fly VM size and memory strings
        # Fly.io requires explicit memory setting; size only controls CPU
        # shared-cpu-1x: 1 shared CPU (custom/sub-1GB memory)
        # shared-cpu-2x: 2 shared CPUs (starter 2GB)
        # shared-cpu-4x: 4 shared CPUs (performance 4GB, pro 8GB)
        if memory_mb >= 4096:
            vm_size = "shared-cpu-4x"
        elif memory_mb >= 1024:
            vm_size = "shared-cpu-2x"
        else:
            vm_size = "shared-cpu-1x"

        # Convert memory_mb to Fly's format (e.g., "512mb", "1gb")
        memory_str = f"{memory_mb // 1024}gb" if memory_mb >= 1024 else f"{memory_mb}mb"

        # Note: No 'strategy = "immediate"' - default strategy preserves health check guarantees
        fly_toml_content = f"""# Auto-generated by runtm for deployment
app = "{app_name}"
primary_region = "iad"

[build]
dockerfile = "Dockerfile"

[http_service]
internal_port = {internal_port}
force_https = true
auto_stop_machines = "suspend"
auto_start_machines = true
min_machines_running = 0

# Health check with generous timeouts for fullstack apps
# grace_period: Time to wait before starting health checks (allows app to start)
# interval: Time between health check attempts
# timeout: Max time to wait for a health check response
[[http_service.checks]]
grace_period = "120s"
interval = "30s"
method = "GET"
path = "{health_check_path}"
timeout = "20s"

[deploy]
# Using default rolling strategy to preserve health check guarantees
# (--strategy immediate breaks health check waits)
wait_timeout = "5m"

[[vm]]
size = "{vm_size}"
memory = "{memory_str}"

# Graceful shutdown
kill_signal = "SIGTERM"
kill_timeout = "30s"
"""
        # Append [[mounts]] sections for persistent volumes
        vol_list = volumes or []
        if vol_list:
            for vol in vol_list:
                fly_toml_content += f"""
[[mounts]]
source = "{vol.name}"
destination = "{vol.path}"
"""
            self._log(
                f"Added {len(vol_list)} volume mount(s) to fly.toml: "
                + ", ".join(f"{v.name}:{v.path}" for v in vol_list),
                logs,
            )

        fly_toml_path.write_text(fly_toml_content)

        # Pre-create Fly volumes before deploy — flyctl deploy expects them to exist
        if vol_list:
            self._log("Creating Fly volumes...", logs)
            try:
                self._ensure_fly_volumes(
                    app_name=app_name,
                    volumes=vol_list,
                    fly_api_token=fly_api_token,
                    region="iad",
                    logs=logs,
                )
            except Exception as e:
                self._log(f"Warning: Volume creation issue: {e}", logs)

        try:
            # Set up environment for flyctl
            env = os.environ.copy()
            env["FLY_API_TOKEN"] = fly_api_token

            self._log("Starting remote build and deploy on Fly.io...", logs)

            # Build base command
            # --image-label: Tag image for rollbacks/reuse
            # --yes: Skip confirmation prompts
            # --wait-timeout: Must exceed grace_period (120s) + time for app to boot and pass checks
            # Note: NO --strategy immediate (breaks health check guarantees)
            # Note: NO --remote-only (redundant with --depot)
            base_cmd = [
                "flyctl",
                "deploy",
                "--app",
                app_name,
                "--image-label",
                clean_id,
                "--yes",
                "--wait-timeout",
                "5m",
            ]

            # Use BuildKit directly — Depot requires mTLS/DEPOT_TOKEN auth that is
            # unavailable inside Fly worker machines; it times out after 5 min before
            # failing. BuildKit uses FLY_API_TOKEN through Fly's registry mirror and
            # works reliably from within Fly machines.
            self._log("Using BuildKit builder...", logs)

            result = subprocess.run(
                base_cmd + ["--buildkit"],
                cwd=str(context_path),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=env,
            )

            # Log output
            if result.stdout:
                for line in result.stdout.strip().split("\n"):
                    if line.strip():
                        self._log(line, logs)

            if result.returncode != 0:
                error_msg = result.stderr.strip() if result.stderr else "Remote build failed"
                # Also include stdout in error for debugging
                if result.stdout:
                    error_msg = f"{error_msg}\n\nBuild output:\n{result.stdout}"
                self._log(f"ERROR: {error_msg}", logs)
                return BuildResult(
                    success=False,
                    error=error_msg,
                    logs=logs,
                )

            # flyctl deploy does full deployment, so we're done
            url = construct_deployment_url(app_name)
            self._log(f"Remote build and deploy complete: {image_tag}", logs)
            self._log(f"Deployed to: {url}", logs)

            return BuildResult(
                success=True,
                image_tag=image_tag,
                image_label=clean_id,
                logs=logs,
                deployed=True,
                url=url,
            )

        except subprocess.TimeoutExpired:
            self._log("Build timeout expired", logs)
            return BuildResult(
                success=False,
                error=f"Build timeout after {timeout_seconds}s",
                logs=logs,
            )
        except FileNotFoundError:
            # flyctl not found - this is a configuration error, not a fallback scenario
            # Local fallback won't work because self.client isn't set when use_remote_builder=True
            error_msg = "flyctl not found in PATH. Install flyctl for remote builder support."
            self._log(f"ERROR: {error_msg}", logs)
            return BuildResult(
                success=False,
                error=error_msg,
                logs=logs,
            )
        except Exception as e:
            self._log(f"Remote build error: {e}", logs)
            return BuildResult(
                success=False,
                error=str(e),
                logs=logs,
            )

    def _ensure_fly_volumes(
        self,
        app_name: str,
        volumes: list,
        fly_api_token: str,
        region: str,
        logs: list[str],
    ) -> None:
        """Create Fly volumes if they don't already exist (idempotent).

        Uses the Fly Machines API directly to create volumes before
        flyctl deploy attaches them via [[mounts]].

        Args:
            app_name: Fly app name
            volumes: VolumeConfig objects (name, path, size_gb)
            fly_api_token: Fly API token
            region: Fly region
            logs: Log buffer
        """
        import httpx

        api_base = "https://api.machines.dev/v1"
        headers = {
            "Authorization": f"Bearer {fly_api_token}",
            "Content-Type": "application/json",
        }

        # List existing volumes
        existing: dict[str, str] = {}
        try:
            resp = httpx.get(
                f"{api_base}/apps/{app_name}/volumes",
                headers=headers,
                timeout=30.0,
            )
            if resp.status_code == 200:
                for vol in resp.json():
                    key = f"{vol.get('name')}:{vol.get('region')}"
                    existing[key] = vol["id"]
        except Exception as e:
            self._log(f"Warning: Could not list existing volumes: {e}", logs)

        for vol in volumes:
            key = f"{vol.name}:{region}"
            if key in existing:
                self._log(f"Volume '{vol.name}' already exists in {region}", logs)
                continue

            try:
                resp = httpx.post(
                    f"{api_base}/apps/{app_name}/volumes",
                    headers=headers,
                    json={
                        "name": vol.name,
                        "region": region,
                        "size_gb": vol.size_gb,
                    },
                    timeout=30.0,
                )
                if resp.status_code >= 400:
                    error = resp.text
                    try:
                        error = resp.json().get("error", error)
                    except Exception:
                        pass
                    self._log(f"Warning: Failed to create volume '{vol.name}': {error}", logs)
                else:
                    vol_id = resp.json().get("id", "unknown")
                    self._log(
                        f"Created volume '{vol.name}' ({vol.size_gb}GB) in {region}: {vol_id}",
                        logs,
                    )
            except Exception as e:
                self._log(f"Warning: Failed to create volume '{vol.name}': {e}", logs)

    def build(
        self,
        context_path: Path,
        image_name: str,
        deployment_id: str,
        timeout_seconds: int = 600,
    ) -> BuildResult:
        """Build a Docker image from a context directory.

        Uses BuildKit for parallel multi-stage builds and better caching.

        Args:
            context_path: Path to build context (extracted artifact)
            image_name: Base name for the image (e.g., app name)
            deployment_id: Deployment ID for tagging
            timeout_seconds: Build timeout

        Returns:
            BuildResult with image tag or error
        """
        logs: list[str] = []
        # Docker tags and Fly app names need lowercase letters, numbers, dashes only
        clean_id = deployment_id[:12].replace("_", "-")
        image_tag = f"{self.registry}/{image_name}:{clean_id}"

        self._log(f"Building image: {image_tag}", logs)
        self._log(f"Context: {context_path}", logs)

        # Check for Dockerfile
        dockerfile_path = context_path / "Dockerfile"
        if not dockerfile_path.exists():
            return BuildResult(
                success=False,
                error="Dockerfile not found in artifact",
                logs=logs,
            )

        try:
            # Build the image for linux/amd64 (Fly.io runs on AMD64)
            # BuildKit is enabled via DOCKER_BUILDKIT=1 env var
            self._log("Starting Docker build with BuildKit for linux/amd64...", logs)

            _, build_logs = self.client.images.build(
                path=str(context_path),
                tag=image_tag,
                rm=True,  # Remove intermediate containers
                timeout=timeout_seconds,
                platform="linux/amd64",  # Fly.io runs on AMD64
                buildargs={
                    "BUILDKIT_INLINE_CACHE": "1",  # Enable inline cache
                },
            )

            # Process build logs
            for chunk in build_logs:
                if "stream" in chunk:
                    line = chunk["stream"].strip()
                    if line:
                        self._log(line, logs)
                elif "error" in chunk:
                    error_msg = chunk["error"].strip()
                    self._log(f"ERROR: {error_msg}", logs)
                    return BuildResult(
                        success=False,
                        error=error_msg,
                        logs=logs,
                    )

            self._log(f"Built image: {image_tag}", logs)

            return BuildResult(
                success=True,
                image_tag=image_tag,
                logs=logs,
            )

        except DockerBuildError as e:
            error_msg = str(e)
            self._log(f"Build failed: {error_msg}", logs)
            return BuildResult(
                success=False,
                error=error_msg,
                logs=logs,
            )
        except DockerException as e:
            error_msg = str(e)
            self._log(f"Docker error: {error_msg}", logs)
            return BuildResult(
                success=False,
                error=error_msg,
                logs=logs,
            )

    def push(
        self,
        image_tag: str,
        timeout_seconds: int = 300,  # noqa: ARG002 - Reserved for future timeout implementation
    ) -> BuildResult:
        """Push a Docker image to the registry.

        Args:
            image_tag: Full image tag to push
            timeout_seconds: Push timeout (reserved for future use)

        Returns:
            BuildResult with success status
        """
        logs: list[str] = []

        self._log(f"Pushing image: {image_tag}", logs)

        try:
            # Push the image
            push_logs = self.client.images.push(
                image_tag,
                stream=True,
                decode=True,
            )

            for chunk in push_logs:
                if "status" in chunk:
                    status = chunk["status"]
                    progress = chunk.get("progress", "")
                    if progress:
                        self._log(f"{status}: {progress}", logs)
                    else:
                        self._log(status, logs)
                elif "error" in chunk:
                    error_msg = chunk["error"].strip()
                    self._log(f"ERROR: {error_msg}", logs)
                    return BuildResult(
                        success=False,
                        image_tag=image_tag,
                        error=error_msg,
                        logs=logs,
                    )

            self._log(f"Pushed image: {image_tag}", logs)

            return BuildResult(
                success=True,
                image_tag=image_tag,
                logs=logs,
            )

        except DockerException as e:
            error_msg = str(e)
            self._log(f"Push failed: {error_msg}", logs)
            return BuildResult(
                success=False,
                image_tag=image_tag,
                error=error_msg,
                logs=logs,
            )

    def build_and_push(
        self,
        artifact_path: Path,
        image_name: str,
        deployment_id: str,
        build_timeout: int = 600,
        push_timeout: int = 300,
        fly_api_token: str | None = None,
        internal_port: int = 3000,
        health_check_path: str = "/health",
        memory_mb: int = 256,
        volumes: list | None = None,
    ) -> BuildResult:
        """Build and deploy a Docker image from an artifact.

        Remote Builder (Default):
            If use_remote_builder is True and fly_api_token is provided,
            uses Fly's remote builder to build AND deploy in one step.
            Returns BuildResult with deployed=True and url.

        Local Build (Fallback):
            Builds locally with Docker, pushes to Fly registry.
            Returns BuildResult with deployed=False (needs separate deploy step).

        Args:
            artifact_path: Path to artifact.zip
            image_name: Base name for the image
            deployment_id: Deployment ID for tagging
            build_timeout: Build timeout in seconds
            push_timeout: Push timeout in seconds
            fly_api_token: Fly.io API token (required for remote builder)
            internal_port: Internal port the app listens on (default 3000)
            health_check_path: Health check endpoint path (default /health)
            memory_mb: Memory in MB (determines VM size)
            volumes: Persistent volume configs (VolumeConfig objects with name, path, size_gb)

        Returns:
            BuildResult with final status
        """
        all_logs: list[str] = []

        # Extract artifact to temp directory
        with tempfile.TemporaryDirectory() as temp_dir:
            context_path = Path(temp_dir) / "context"
            context_path.mkdir()

            self._log("Extracting artifact...", all_logs)
            try:
                self.extract_artifact(artifact_path, context_path)
            except BuildError as e:
                all_logs.append(str(e))
                return BuildResult(
                    success=False,
                    error=str(e),
                    logs=all_logs,
                )

            # Extract discovery metadata if present
            discovery_json: dict | None = None
            discovery_path = context_path / "runtm.discovery.yaml"
            if discovery_path.exists():
                try:
                    discovery = AppDiscovery.from_file(discovery_path)
                    discovery_json = discovery.model_dump(exclude_none=True)
                    self._log("Found runtm.discovery.yaml", all_logs)
                except Exception as e:
                    self._log(f"Warning: Could not parse runtm.discovery.yaml: {e}", all_logs)

            # Use remote builder if enabled and token available
            if self.use_remote_builder:
                if not fly_api_token:
                    # Remote builder requires API token - fail fast with clear error
                    error_msg = "FLY_API_TOKEN is required for remote builder"
                    self._log(f"ERROR: {error_msg}", all_logs)
                    return BuildResult(
                        success=False,
                        error=error_msg,
                        logs=all_logs,
                    )
                self._log("Using Fly remote builder (builds and deploys)...", all_logs)
                result = self.build_remote(
                    context_path=context_path,
                    app_name=image_name,
                    deployment_id=deployment_id,
                    fly_api_token=fly_api_token,
                    timeout_seconds=build_timeout,
                    internal_port=internal_port,
                    health_check_path=health_check_path,
                    memory_mb=memory_mb,
                    volumes=volumes,
                )
                all_logs.extend(result.logs)
                return BuildResult(
                    success=result.success,
                    image_tag=result.image_tag,
                    image_label=result.image_label,
                    error=result.error,
                    logs=all_logs,
                    deployed=result.deployed,
                    url=result.url,
                    discovery_json=discovery_json,
                )

            # Fall back to local build + push (only when use_remote_builder=False)
            # Build image
            build_result = self.build(
                context_path=context_path,
                image_name=image_name,
                deployment_id=deployment_id,
                timeout_seconds=build_timeout,
            )
            all_logs.extend(build_result.logs)

            if not build_result.success:
                return BuildResult(
                    success=False,
                    error=build_result.error,
                    logs=all_logs,
                    discovery_json=discovery_json,
                )

            # Push image
            push_result = self.push(
                image_tag=build_result.image_tag,
                timeout_seconds=push_timeout,
            )
            all_logs.extend(push_result.logs)

            return BuildResult(
                success=push_result.success,
                image_tag=build_result.image_tag,
                error=push_result.error,
                logs=all_logs,
                discovery_json=discovery_json,
            )

    def login_fly_registry(self, api_token: str) -> bool:
        """Login to Fly.io registry.

        Args:
            api_token: Fly.io API token

        Returns:
            True if login successful
        """
        if self.use_remote_builder:
            # Remote builder doesn't need local login
            return True

        try:
            self.client.login(
                username="x",
                password=api_token,
                registry=self.registry,
            )
            return True
        except DockerException:
            return False
