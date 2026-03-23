"""Deploy job - main worker pipeline with optimized builds."""

from __future__ import annotations

import concurrent.futures
import contextlib
import os

from sqlalchemy.orm import Session

from runtm_shared import Manifest
from runtm_shared.errors import (
    BuildError,
    DeploymentNotFoundError,
    DeploymentStateError,
    DeployTimeoutError,
    HealthCheckError,
)
from runtm_shared.storage.base import ArtifactStore
from runtm_shared.types import (
    DeploymentState,
    Limits,
    LogType,
    MachineConfig,
    can_transition,
    get_tier_spec,
)
from runtm_shared.urls import construct_deployment_url, get_subdomain_for_app
from runtm_worker.builder import DockerBuilder
from runtm_worker.logs import LogCapture
from runtm_worker.providers import FlyProvider


class DeployJob:
    """Main deploy job that orchestrates the build/deploy pipeline.

    Pipeline (Remote Builder - Default, Fastest):
        1. Load deployment from DB
        2. Validate state (must be QUEUED)
        3. Transition to BUILDING
        4. Create Fly app and allocate IPs
        5. Build AND deploy via Fly remote builder (single step)
        6. Transition to DEPLOYING
        7. Save provider resource and verify health
        8. Transition to READY with URL
        9. Handle errors -> FAILED with error message

    Pipeline (Local Build - Fallback):
        1-4. Same as above
        5. Build Docker image locally with BuildKit
        6. Push image to Fly registry
        7. Transition to DEPLOYING
        8. Create machine via Fly Machines API
        9. Wait for health check
        10. Transition to READY with URL

    Optimizations:
        - Remote builder (default): Build AND deploy on Fly's infra in one step
        - Auto-generated fly.toml with optimized health check settings
        - BuildKit: Parallel multi-stage builds (local fallback)
        - Concurrent operations: Parallel IP allocation

    Redeployment:
        When redeploy_from is provided, the job will:
        - Use the existing Fly app and machine from the previous deployment
        - Update the machine with the new image instead of creating a new app
        - Keep the same URL
    """

    def __init__(
        self,
        db: Session,
        storage: ArtifactStore,
        fly_api_token: str | None = None,
        redeploy_from: str | None = None,
        use_remote_builder: bool = True,
        secrets: dict | None = None,
        config_only: bool = False,
        allow_local_builds: bool | None = None,
        deploy_provider: str | None = None,
    ):
        """Initialize deploy job.

        Args:
            db: Database session
            storage: Artifact store (LocalFileStore for dev, S3FileStore for prod)
            fly_api_token: Fly.io API token
            redeploy_from: Previous deployment ID for redeployments
            use_remote_builder: Use Fly's remote builder (faster, recommended)
            secrets: Secrets to inject (passed through to provider, never stored)
            config_only: Skip Docker build and reuse previous image
            allow_local_builds: Explicit override for local builds (dev only)
            deploy_provider: Provider name ("local" or "fly"). Defaults to
                DEPLOY_PROVIDER env var, falling back to "local".
        """
        self.db = db
        self.storage = storage
        self.fly_api_token = fly_api_token or os.environ.get("FLY_API_TOKEN")
        self.redeploy_from = redeploy_from
        self.use_remote_builder = use_remote_builder
        self.secrets = secrets or {}
        self.config_only = config_only
        self.deploy_provider = (
            deploy_provider or os.environ.get("DEPLOY_PROVIDER", "local")
        ).lower()
        # SECURITY: Allow local builds only with explicit opt-in
        if allow_local_builds is None:
            self.allow_local_builds = os.environ.get("ALLOW_LOCAL_BUILDS", "").lower() == "true"
        else:
            self.allow_local_builds = allow_local_builds

    def _get_deployment(self, deployment_id: str):
        """Get deployment by human-friendly ID.

        Args:
            deployment_id: Deployment ID (e.g., dep_abc123)

        Returns:
            Deployment record

        Raises:
            DeploymentNotFoundError: If not found
        """
        # Import here to avoid circular imports
        from runtm_api.db.models import Deployment

        deployment = (
            self.db.query(Deployment).filter(Deployment.deployment_id == deployment_id).first()
        )
        if not deployment:
            raise DeploymentNotFoundError(deployment_id)
        return deployment

    def _transition_state(
        self,
        deployment,
        new_state: DeploymentState,
        error_message: str | None = None,
        url: str | None = None,
    ) -> None:
        """Transition deployment to a new state.

        Args:
            deployment: Deployment record
            new_state: Target state
            error_message: Error message (for FAILED state)
            url: Deployment URL (for READY state)

        Raises:
            DeploymentStateError: If transition not allowed
        """
        from datetime import datetime, timezone

        current_state = deployment.state
        if not can_transition(current_state, new_state):
            raise DeploymentStateError(current_state.value, new_state.value)

        deployment.state = new_state
        if error_message:
            deployment.error_message = error_message
        if url:
            deployment.url = url

        # Capture the completion timestamp when transitioning to READY
        # This is the actual deploy time, not affected by subsequent updates
        if new_state == DeploymentState.READY:
            deployment.ready_at = datetime.now(timezone.utc)

        self.db.commit()

    def _save_provider_resource(
        self,
        deployment,
        resource,
        image_label: str | None = None,
        provider_name: str = "fly",
    ) -> None:
        """Save provider resource mapping to DB.

        Args:
            deployment: Deployment record
            resource: ProviderResource from deploy
            image_label: Optional image label for rollbacks/reuse
            provider_name: Provider identifier stored in DB (default "fly"
                for backward compatibility; "local" for local deploys)
        """
        from runtm_api.db.models import ProviderResource as ProviderResourceModel

        pr = ProviderResourceModel(
            deployment_id=deployment.id,
            provider=provider_name,
            app_name=resource.app_name,
            machine_id=resource.machine_id,
            region=resource.region,
            image_ref=resource.image_ref,
            image_label=image_label,
        )
        self.db.add(pr)
        self.db.commit()

    def _get_previous_provider_resource(self, previous_deployment_id: str):
        """Get provider resource from a previous deployment.

        Args:
            previous_deployment_id: Previous deployment ID

        Returns:
            Tuple of (ProviderResource, image_label) from the previous deployment, or (None, None)
        """
        from runtm_api.db.models import Deployment
        from runtm_shared.types import ProviderResource

        previous = (
            self.db.query(Deployment)
            .filter(Deployment.deployment_id == previous_deployment_id)
            .first()
        )

        if not previous or not previous.provider_resource:
            return None, None

        pr = previous.provider_resource
        resource = ProviderResource(
            app_name=pr.app_name,
            machine_id=pr.machine_id,
            region=pr.region,
            image_ref=pr.image_ref,
            url=previous.url or construct_deployment_url(pr.app_name),
        )
        return resource, pr.image_label

    def _get_dns_provider(self):
        """Get configured DNS provider instance.

        Returns:
            DnsProvider instance or None if not configured
        """
        from runtm_api.core.config import get_settings

        settings = get_settings()

        if not settings.dns_enabled:
            return None

        if settings.dns_provider == "cloudflare":
            from runtm_shared.dns.cloudflare import CloudflareDnsProvider

            if not settings.cloudflare_api_token or not settings.cloudflare_zone_id:
                return None

            return CloudflareDnsProvider(
                api_token=settings.cloudflare_api_token,
                zone_id=settings.cloudflare_zone_id,
            )

        return None

    def _inject_secrets(
        self,
        app_name: str,
        deploy_log,
        stage: bool = False,
    ) -> bool:
        """Inject secrets to the deployment provider.

        Secrets are passed directly to the provider and are NEVER stored
        in the Runtm database. Only secret NAMES are logged.

        Args:
            app_name: Provider app name (e.g., Fly app name)
            deploy_log: Log capture for writing messages
            stage: If True, stage secrets without releasing (picked up by next deploy)

        Returns:
            True if secrets were injected successfully (or none to inject)
        """
        if not self.secrets:
            return True

        from runtm_worker.providers import FlySecretsProvider

        # Log only secret NAMES, never values
        secret_names = list(self.secrets.keys())
        action = "Staging" if stage else "Injecting"
        deploy_log.write(f"{action} {len(self.secrets)} secrets: {', '.join(secret_names)}")

        secrets_provider = FlySecretsProvider(api_token=self.fly_api_token)
        result = secrets_provider.set_secrets(app_name, self.secrets, stage=stage)

        if result.success:
            status = "staged" if stage else "injected"
            deploy_log.write(f"Secrets {status} successfully ({result.secrets_set} secrets)")
            return True
        else:
            deploy_log.write(f"Warning: Failed to inject secrets: {result.error}")
            return False

    def _provision_custom_subdomain(
        self,
        provider: FlyProvider,
        app_name: str,
        deploy_log,
    ) -> None:
        """Provision custom subdomain with DNS record and SSL certificate.

        When RUNTM_BASE_DOMAIN is configured (e.g., runtm.com), this method:
        1. Creates a CNAME record via DNS provider (Cloudflare):
           runtm-abc123.runtm.com -> runtm-abc123.fly.dev
        2. Adds SSL certificate for the subdomain to the Fly app

        This hides provider URLs - users only see runtm.com URLs.

        Args:
            provider: Fly provider instance (for SSL cert)
            app_name: Fly app name (e.g., "runtm-abc123")
            deploy_log: Log capture for writing messages
        """
        from runtm_shared.urls import get_base_domain

        base_domain = get_base_domain()
        subdomain = get_subdomain_for_app(app_name)

        if not subdomain or not base_domain:
            # No custom domain configured, skip
            return

        deploy_log.write(f"Provisioning custom subdomain: {subdomain}")

        # Step 1: Create DNS CNAME record
        dns_provider = self._get_dns_provider()
        if dns_provider:
            try:
                # Target is the provider's native URL (e.g., runtm-abc123.fly.dev)
                target = f"{app_name}.fly.dev"

                success = dns_provider.upsert_cname(
                    subdomain=app_name,
                    domain=base_domain,
                    target=target,
                    proxied=False,  # Don't proxy - let Fly handle SSL
                )

                if success:
                    deploy_log.write(f"DNS record created: {subdomain} -> {target}")
                else:
                    deploy_log.write(f"Warning: Failed to create DNS record for {subdomain}")

            except Exception as e:
                deploy_log.write(f"Warning: DNS record creation failed: {e}")
                # Continue to try adding certificate anyway
        else:
            deploy_log.write("Warning: DNS provider not configured, skipping DNS record")
            deploy_log.write("DNS record must be created manually for custom domain to work")

        # Step 2: Add SSL certificate to Fly app
        try:
            from runtm_shared.types import ProviderResource

            # Create a minimal resource for the add_custom_domain call
            resource = ProviderResource(
                app_name=app_name,
                machine_id="",
                region="",
                image_ref="",
                url="",
            )

            # Add certificate for the subdomain
            domain_info = provider.add_custom_domain(resource, subdomain)

            if domain_info.certificate_status == "issued":
                deploy_log.write(f"SSL certificate ready: https://{subdomain}")
            elif domain_info.certificate_status in ("pending", "awaiting_dns"):
                deploy_log.write(f"SSL certificate pending (awaiting DNS): {subdomain}")
            else:
                deploy_log.write(f"SSL certificate status: {domain_info.certificate_status}")

        except Exception as e:
            # Non-fatal: deployment still works on fly.dev
            deploy_log.write(f"Warning: Could not provision SSL certificate: {e}")
            deploy_log.write(f"App is still accessible at https://{app_name}.fly.dev")

    def _ensure_fly_app_with_ips(
        self,
        provider: FlyProvider,
        app_name: str,
        build_log,
    ) -> bool:
        """Ensure Fly app exists and has IP addresses allocated.

        Uses concurrent execution for IP allocation to save time.

        Args:
            provider: Fly provider instance
            app_name: App name to create/check
            build_log: Log capture for writing messages

        Returns:
            True if app was created (new), False if it already existed
        """
        build_log.write(f"Ensuring Fly app exists: {app_name}")

        existing_app = provider._get_app(app_name)
        if existing_app:
            build_log.write(f"Using existing Fly app: {app_name}")
            return False

        # Create app
        provider._create_app(app_name)
        build_log.write(f"Created Fly app: {app_name}")

        # Allocate IP addresses concurrently
        build_log.write("Allocating IP addresses...")

        # Use ThreadPoolExecutor for concurrent IP allocation
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            future = executor.submit(provider._allocate_ip_addresses, app_name)
            allocated_ips = future.result(timeout=30)

        if allocated_ips:
            build_log.write(f"Allocated IPs: {', '.join(allocated_ips)}")
        else:
            build_log.write("Warning: Could not allocate IP addresses")

        return True

    def _run_local(
        self,
        deployment,
        deployment_id: str,
        manifest: Manifest,
        is_redeployment: bool,
        previous_resource,
    ) -> bool:
        """Run the deploy pipeline using the local Docker provider.

        Builds the image locally (no registry push) and deploys it as a
        Docker container on the host via LocalProvider.
        """
        _artifact_path_to_cleanup = None

        try:
            # === BUILD PHASE ===
            self._transition_state(deployment, DeploymentState.BUILDING)

            with LogCapture(self.db, str(deployment.id), LogType.BUILD) as build_log:
                build_log.write(f"Processing deployment (local): {deployment_id}")
                build_log.write(f"Name: {manifest.name}")

                artifact_path = self.storage.get_path(deployment.artifact_key)
                if not artifact_path.exists():
                    raise BuildError(f"Artifact not found: {deployment.artifact_key}")

                _artifact_path_to_cleanup = artifact_path

                builder = DockerBuilder(
                    registry="runtm-local",
                    log_callback=build_log.write,
                    use_remote_builder=False,
                )

                app_name = (
                    previous_resource.app_name
                    if is_redeployment and previous_resource
                    else f"runtm-{deployment_id[:12]}".replace("_", "-")
                )

                build_result = builder.build_local_only(
                    artifact_path=artifact_path,
                    image_name=app_name,
                    deployment_id=deployment_id,
                )

                if not build_result.success:
                    raise BuildError(build_result.error or "Local build failed")

                image_tag = build_result.image_tag
                build_log.write(f"Built: {image_tag}")

                if build_result.discovery_json:
                    deployment.discovery_json = build_result.discovery_json
                    self.db.commit()

            # === DEPLOY PHASE ===
            self._transition_state(deployment, DeploymentState.DEPLOYING)

            with LogCapture(self.db, str(deployment.id), LogType.DEPLOY) as deploy_log:
                if self.secrets:
                    deploy_log.add_redact_values(self.secrets)

                from runtm_worker.providers.factory import get_provider

                provider = get_provider("local")

                machine_tier = manifest.get_machine_tier()
                tier_spec = get_tier_spec(machine_tier)
                deploy_log.write(f"Machine tier: {tier_spec.description}")

                env = dict(self.secrets) if self.secrets else {}
                config = MachineConfig.from_tier(
                    tier=machine_tier,
                    image=image_tag,
                    health_check_path=manifest.health_path,
                    internal_port=manifest.port,
                    env=env,
                )

                if is_redeployment and previous_resource:
                    deploy_log.write(f"Redeploying to existing container: {previous_resource.app_name}")
                    result = provider.redeploy(previous_resource, config)
                else:
                    deploy_log.write("Deploying to local Docker container...")
                    result = provider.deploy(deployment_id, config)

                deploy_log.write_lines(result.logs.split("\n"))

                if not result.success:
                    raise BuildError(result.error or "Local deploy failed")

                self._save_provider_resource(
                    deployment,
                    result.resource,
                    build_result.image_label,
                    provider_name="local",
                )
                deploy_log.write(f"Provider resource saved: {result.resource.app_name}")

                deploy_log.write("Checking health...")
                healthy = provider.health_check(
                    result.resource,
                    path=manifest.health_path,
                    timeout_seconds=60,
                )
                if healthy:
                    deploy_log.write("Health check passed!")
                else:
                    deploy_log.write("Warning: health check did not pass within 60 s")

                deploy_log.write(f"URL: {result.resource.url}")

            self._transition_state(
                deployment,
                DeploymentState.READY,
                url=result.resource.url,
            )
            return True

        except Exception as e:
            error_message = str(e)
            if deployment:
                with contextlib.suppress(DeploymentStateError):
                    self._transition_state(
                        deployment,
                        DeploymentState.FAILED,
                        error_message=error_message,
                    )
            return False

        finally:
            if _artifact_path_to_cleanup is not None:
                self.storage.cleanup_path(_artifact_path_to_cleanup)

    def run(self, deployment_id: str) -> bool:
        """Run the deploy pipeline.

        Args:
            deployment_id: Deployment ID to process

        Returns:
            True if deployment succeeded, False otherwise
        """
        deployment = None
        _artifact_path_to_cleanup = None

        try:
            # Load deployment
            deployment = self._get_deployment(deployment_id)

            # Validate state
            if deployment.state != DeploymentState.QUEUED:
                raise DeploymentStateError(
                    deployment.state.value,
                    DeploymentState.BUILDING.value,
                )

            # Get manifest
            manifest = Manifest.model_validate(deployment.manifest_json)

            # Check for redeployment - compute ONCE before build phase
            # This ensures both remote and local builder paths use the same logic
            previous_resource = None
            previous_image_label = None
            is_redeployment = False
            if self.redeploy_from:
                previous_resource, previous_image_label = self._get_previous_provider_resource(
                    self.redeploy_from
                )
                if previous_resource:
                    is_redeployment = True

            # === LOCAL DEPLOY PATH ===
            if self.deploy_provider == "local":
                return self._run_local(
                    deployment,
                    deployment_id,
                    manifest,
                    is_redeployment,
                    previous_resource,
                )

            # === CONFIG-ONLY DEPLOY PATH ===
            # Skip build entirely and reuse previous image (for env var/tier changes)
            if self.config_only:
                if not is_redeployment or not previous_resource or not previous_image_label:
                    raise BuildError(
                        "Config-only deploy requires a previous deployment with a valid image. "
                        "Use a regular deploy instead."
                    )

                # Skip BUILDING state, go directly to DEPLOYING
                self._transition_state(deployment, DeploymentState.DEPLOYING)

                with LogCapture(
                    self.db,
                    str(deployment.id),
                    LogType.DEPLOY,
                ) as deploy_log:
                    # Add secret values for redaction (never log actual values)
                    if self.secrets:
                        deploy_log.add_redact_values(self.secrets)

                    deploy_log.write("Config-only deployment - skipping Docker build")
                    deploy_log.write(
                        f"Reusing image: {previous_resource.app_name}:{previous_image_label}"
                    )

                    # Get machine tier from manifest
                    machine_tier = manifest.get_machine_tier()
                    tier_spec = get_tier_spec(machine_tier)
                    deploy_log.write(f"Machine tier: {tier_spec.description}")

                    # Use flyctl deploy --image to deploy the existing image
                    import subprocess

                    app_name = previous_resource.app_name
                    image_ref = f"registry.fly.io/{app_name}:{previous_image_label}"

                    # Stage secrets BEFORE deploy - they'll be picked up by flyctl deploy
                    # This avoids a second release/restart after deploy completes
                    if self.secrets:
                        self._inject_secrets(app_name, deploy_log, stage=True)

                    env = os.environ.copy()
                    env["FLY_API_TOKEN"] = self.fly_api_token

                    deploy_log.write(f"Deploying image: {image_ref}")

                    cmd = [
                        "flyctl",
                        "deploy",
                        "--app",
                        app_name,
                        "--image",
                        image_ref,
                        "--yes",
                        "--wait-timeout",
                        "3m",
                    ]

                    result = subprocess.run(
                        cmd,
                        capture_output=True,
                        text=True,
                        timeout=300,
                        env=env,
                    )

                    # Log output
                    if result.stdout:
                        for line in result.stdout.strip().split("\n"):
                            if line.strip():
                                deploy_log.write(line)

                    if result.returncode != 0:
                        error_msg = (
                            result.stderr.strip() if result.stderr else "Config-only deploy failed"
                        )
                        deploy_log.write(f"ERROR: {error_msg}")
                        raise DeployTimeoutError(300)

                    # Secrets were staged before deploy, no need to inject again

                    # Save provider resource (reuse previous but update deployment link)
                    from runtm_shared.types import ProviderResource

                    resource = ProviderResource(
                        app_name=app_name,
                        machine_id=previous_resource.machine_id,
                        region=previous_resource.region,
                        image_ref=image_ref,
                        url=previous_resource.url,
                    )
                    self._save_provider_resource(deployment, resource, previous_image_label)

                    deploy_log.write(f"URL: {previous_resource.url}")

                # === SUCCESS (config-only) ===
                self._transition_state(
                    deployment,
                    DeploymentState.READY,
                    url=previous_resource.url,
                )
                return True

            # === BUILD PHASE ===
            self._transition_state(deployment, DeploymentState.BUILDING)

            with LogCapture(
                self.db,
                str(deployment.id),
                LogType.BUILD,
            ) as build_log:
                build_log.write(f"Processing deployment: {deployment_id}")
                build_log.write(f"Name: {manifest.name}")
                build_log.write(f"Template: {manifest.template}")

                # Get artifact (remote backends download to a temp file)
                artifact_path = self.storage.get_path(deployment.artifact_key)
                if not artifact_path.exists():
                    raise BuildError(f"Artifact not found: {deployment.artifact_key}")

                build_log.write(f"Artifact: {artifact_path}")
                _artifact_path_to_cleanup = artifact_path

                # Build Docker image
                builder = DockerBuilder(
                    registry="registry.fly.io",
                    log_callback=build_log.write,
                    use_remote_builder=self.use_remote_builder,
                )

                # Determine app name - reuse existing for redeployments (idempotency)
                if is_redeployment and previous_resource:
                    app_name = previous_resource.app_name
                    build_log.write(f"Redeployment detected - reusing existing app: {app_name}")
                    build_log.write(f"Previous URL: {previous_resource.url}")
                else:
                    # New deployment - generate app name from deployment ID
                    # Fly app names: lowercase letters, numbers, dashes only (no underscores)
                    app_name = f"runtm-{deployment_id[:12]}".replace("_", "-")
                    build_log.write(f"New deployment - creating app: {app_name}")

                # Login to Fly registry (skipped for remote builder)
                if self.fly_api_token and not self.use_remote_builder:
                    # SECURITY: Local builds require explicit opt-in
                    if not self.allow_local_builds:
                        raise BuildError(
                            "Local builds are disabled. Set USE_REMOTE_BUILDER=true (recommended) "
                            "or ALLOW_LOCAL_BUILDS=true for development only."
                        )
                    if not os.path.exists("/var/run/docker.sock"):
                        raise BuildError(
                            "Local builds require Docker socket. Mount /var/run/docker.sock "
                            "or use USE_REMOTE_BUILDER=true."
                        )
                    build_log.write("Logging in to Fly registry...")
                    if not builder.login_fly_registry(self.fly_api_token):
                        raise BuildError("Failed to login to Fly registry")

                # Create Fly app BEFORE pushing (registry requires app to exist)
                # For redeployments, the app already exists so this is a no-op
                provider = FlyProvider(api_token=self.fly_api_token)
                try:
                    self._ensure_fly_app_with_ips(provider, app_name, build_log)
                except Exception as e:
                    raise BuildError(f"Failed to create Fly app: {e}") from e

                # Stage secrets BEFORE deploy - they'll be picked up by flyctl deploy
                # This avoids a second release/restart after deploy completes
                if self.secrets:
                    build_log.add_redact_values(self.secrets)
                    self._inject_secrets(app_name, build_log, stage=True)

                # Get machine tier from manifest (defaults to starter)
                machine_tier = manifest.get_machine_tier()
                tier_spec = get_tier_spec(machine_tier)

                # Build and push (or use remote builder which also deploys)
                if self.use_remote_builder:
                    build_log.write("Using Fly remote builder (builds and deploys)...")

                build_result = builder.build_and_push(
                    artifact_path=artifact_path,
                    image_name=app_name,
                    deployment_id=deployment_id,
                    build_timeout=Limits.BUILD_TIMEOUT_SECONDS,
                    fly_api_token=self.fly_api_token,
                    internal_port=manifest.port,
                    health_check_path=manifest.health_path,
                    memory_mb=tier_spec.memory_mb,
                )

                if not build_result.success:
                    raise BuildError(build_result.error or "Build failed")

                image_tag = build_result.image_tag
                build_log.write(f"Built: {image_tag}")

                # Save discovery metadata if found
                if build_result.discovery_json:
                    deployment.discovery_json = build_result.discovery_json
                    self.db.commit()
                    build_log.write("Discovery metadata saved")

            # Check if remote builder already deployed
            if build_result.deployed and build_result.url:
                # Remote builder did the full deployment
                self._transition_state(deployment, DeploymentState.DEPLOYING)

                with LogCapture(
                    self.db,
                    str(deployment.id),
                    LogType.DEPLOY,
                ) as deploy_log:
                    # Add secret values for redaction (never log actual values)
                    if self.secrets:
                        deploy_log.add_redact_values(self.secrets)
                    deploy_log.write("Deployment completed by remote builder")
                    deploy_log.write(f"Machine tier: {tier_spec.description}")
                    deploy_log.write("Auto-stop enabled for cost savings")

                    # Create provider to get machine info and save resource
                    provider = FlyProvider(api_token=self.fly_api_token)

                    # Get machine ID from the app
                    machines = provider._list_machines(app_name)
                    if machines:
                        machine = machines[0]
                        machine_id = machine.get("id", "unknown")
                        region = machine.get("region", "iad")
                    else:
                        machine_id = "unknown"
                        region = "iad"

                    # Save provider resource
                    from runtm_shared.types import ProviderResource

                    resource = ProviderResource(
                        app_name=app_name,
                        machine_id=machine_id,
                        region=region,
                        image_ref=image_tag,
                        url=build_result.url,
                    )
                    # Pass image_label from build result for rollbacks
                    self._save_provider_resource(deployment, resource, build_result.image_label)
                    deploy_log.write(f"Provider resource saved: {app_name}")

                    # Skip redundant health check - flyctl deploy --wait-timeout already verified health
                    # The remote builder only returns success if health checks pass
                    deploy_log.write("Health verified by remote builder")

                    # Secrets were staged before deploy, no need to inject again

                    # Provision custom subdomain certificate if configured
                    self._provision_custom_subdomain(provider, app_name, deploy_log)

                    deploy_log.write(f"URL: {build_result.url}")

                # === SUCCESS ===
                self._transition_state(
                    deployment,
                    DeploymentState.READY,
                    url=build_result.url,
                )

            else:
                # === DEPLOY PHASE (local build path) ===
                self._transition_state(deployment, DeploymentState.DEPLOYING)

                with LogCapture(
                    self.db,
                    str(deployment.id),
                    LogType.DEPLOY,
                ) as deploy_log:
                    # Add secret values for redaction (never log actual values)
                    if self.secrets:
                        deploy_log.add_redact_values(self.secrets)

                    # Create provider
                    provider = FlyProvider(api_token=self.fly_api_token)

                    deploy_log.write(f"Machine tier: {tier_spec.description}")
                    deploy_log.write("Auto-stop enabled for cost savings")

                    # Configure machine using tier specs
                    config = MachineConfig.from_tier(
                        tier=machine_tier,
                        image=image_tag,
                        health_check_path=manifest.health_path,
                        internal_port=manifest.port,
                    )

                    # Use redeployment info computed earlier (same as remote builder path)
                    if is_redeployment and previous_resource:
                        # Redeployment - update existing machine
                        deploy_log.write(
                            f"Redeploying to existing app: {previous_resource.app_name}"
                        )
                        deploy_log.write(f"Previous version URL: {previous_resource.url}")
                        result = provider.redeploy(previous_resource, config)
                    else:
                        # New deployment
                        deploy_log.write("Starting deployment to Fly.io...")
                        result = provider.deploy(deployment_id, config)

                    deploy_log.write_lines(result.logs.split("\n"))

                    if not result.success:
                        raise DeployTimeoutError(Limits.DEPLOY_TIMEOUT_SECONDS)

                    # Save provider resource
                    self._save_provider_resource(deployment, result.resource)
                    deploy_log.write(f"Provider resource saved: {result.resource.app_name}")

                    # Wait for health check
                    deploy_log.write("Waiting for health check...")
                    status = provider.get_status(result.resource)
                    if not status.healthy:
                        # Give it a moment and retry
                        import time

                        time.sleep(10)
                        status = provider.get_status(result.resource)

                    if not status.healthy:
                        raise HealthCheckError(manifest.health_path)

                    deploy_log.write("Health check passed!")

                    # Secrets were staged before build, machine has them already

                    # Provision custom subdomain certificate if configured
                    self._provision_custom_subdomain(provider, result.resource.app_name, deploy_log)

                    deploy_log.write(f"URL: {result.resource.url}")

                # === SUCCESS ===
                self._transition_state(
                    deployment,
                    DeploymentState.READY,
                    url=result.resource.url,
                )

            return True

        except Exception as e:
            # Handle failure
            error_message = str(e)

            if deployment:
                with contextlib.suppress(DeploymentStateError):
                    self._transition_state(
                        deployment,
                        DeploymentState.FAILED,
                        error_message=error_message,
                    )

            return False

        finally:
            if _artifact_path_to_cleanup is not None:
                self.storage.cleanup_path(_artifact_path_to_cleanup)


def process_deployment(
    deployment_id: str,
    redeploy_from: str | None = None,
    use_remote_builder: bool | None = None,
    secrets: dict | None = None,
    config_only: bool = False,
) -> bool:
    """Process a deployment job.

    This is the function called by the worker queue.

    Args:
        deployment_id: Deployment ID to process
        redeploy_from: If this is a redeployment, the previous deployment ID
                      to get existing infrastructure from
        use_remote_builder: Use Fly's remote builder (defaults to config setting)
        secrets: Secrets to inject to the provider (passed through, never stored)
        config_only: Skip Docker build and reuse previous image

    Returns:
        True if successful

    Security note:
        Secrets are passed directly to the SecretsProvider and are NEVER
        stored in the Runtm database. Only secret NAMES are logged.
    """
    from runtm_api.core.config import get_settings
    from runtm_api.db import create_session
    from runtm_shared.storage import get_artifact_store

    settings = get_settings()
    db = create_session()

    # Use config setting if not explicitly provided
    if use_remote_builder is None:
        use_remote_builder = settings.use_remote_builder

    storage = get_artifact_store(
        backend=settings.artifact_storage_backend,
        storage_path=settings.artifact_storage_path,
        s3_bucket=settings.s3_bucket,
        s3_endpoint_url=settings.s3_endpoint_url or None,
        s3_region=settings.s3_region,
    )

    deploy_provider = os.environ.get("DEPLOY_PROVIDER", "local")

    try:
        job = DeployJob(
            db=db,
            storage=storage,
            redeploy_from=redeploy_from,
            use_remote_builder=use_remote_builder,
            secrets=secrets,
            config_only=config_only,
            allow_local_builds=settings.allow_local_builds,
            deploy_provider=deploy_provider,
        )
        return job.run(deployment_id)
    finally:
        db.close()
