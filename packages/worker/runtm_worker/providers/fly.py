"""Fly.io Machines provider implementation."""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from runtm_shared.errors import FlyError, ProviderNotConfiguredError
from runtm_shared.types import CustomDomainInfo, DnsRecord, MachineConfig, ProviderResource
from runtm_shared.urls import construct_deployment_url

from .base import DeployProvider, DeployResult, ProviderStatus


class FlyProvider(DeployProvider):
    """Fly.io Machines API provider.

    Deploys containers as Fly Machines and manages their lifecycle.

    Note: When using the remote builder (default), most deployment is handled
    by flyctl deploy in DockerBuilder.build_remote(). This provider is still
    used for:
        - Creating Fly apps and allocating IPs (before remote build)
        - Listing machines to get machine IDs after remote deploy
        - Health check verification
        - Custom domain management
        - Destroying deployments
        - Local build fallback path

    Environment variables:
        FLY_API_TOKEN: Fly.io API token (required)
        FLY_ORG: Fly.io organization slug (optional, defaults to personal)
    """

    FLY_API_BASE = "https://api.machines.dev/v1"
    FLY_GRAPHQL_URL = "https://api.fly.io/graphql"

    def __init__(
        self,
        api_token: str | None = None,
        org: str | None = None,
    ):
        """Initialize Fly.io provider.

        Args:
            api_token: Fly.io API token (defaults to FLY_API_TOKEN env var)
            org: Fly.io organization (defaults to FLY_ORG env var)
        """
        self.api_token = api_token or os.environ.get("FLY_API_TOKEN")
        self.org = org or os.environ.get("FLY_ORG", "personal")

        if not self.api_token:
            raise ProviderNotConfiguredError("fly")

    @property
    def name(self) -> str:
        return "fly"

    def _headers(self) -> dict[str, str]:
        """Get API request headers."""
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> httpx.Response:
        """Make an API request.

        Args:
            method: HTTP method
            path: API path
            json: Request body
            timeout: Request timeout

        Returns:
            API response

        Raises:
            FlyError: If request fails
        """
        url = f"{self.FLY_API_BASE}{path}"

        try:
            response = httpx.request(
                method=method,
                url=url,
                headers=self._headers(),
                json=json,
                timeout=timeout,
            )

            if response.status_code >= 400:
                error_detail = response.text
                try:
                    error_json = response.json()
                    error_detail = error_json.get("error", error_detail)
                except Exception:
                    pass
                raise FlyError(error_detail, status_code=response.status_code)

            return response
        except httpx.RequestError as e:
            raise FlyError(f"Request failed: {e}") from e

    def _create_app(self, app_name: str) -> dict[str, Any]:
        """Create a Fly app.

        Args:
            app_name: App name

        Returns:
            App details
        """
        response = self._request(
            "POST",
            "/apps",
            json={
                "app_name": app_name,
                "org_slug": self.org,
            },
        )
        return response.json()

    def _allocate_ip_addresses(self, app_name: str) -> list[str]:
        """Allocate IP addresses for the app using GraphQL API.

        This is required for the app to be accessible on the .fly.dev domain.
        Allocates both a shared IPv4 and an IPv6 address.

        Args:
            app_name: App name

        Returns:
            List of allocated IP addresses
        """
        allocated_ips = []

        # Allocate shared IPv4 (free tier)
        ipv4_mutation = """
        mutation($input: AllocateIPAddressInput!) {
            allocateIpAddress(input: $input) {
                ipAddress {
                    id
                    address
                    type
                }
            }
        }
        """

        try:
            response = httpx.post(
                self.FLY_GRAPHQL_URL,
                headers={
                    "Authorization": f"Bearer {self.api_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "query": ipv4_mutation,
                    "variables": {
                        "input": {
                            "appId": app_name,
                            "type": "shared_v4",
                        }
                    },
                },
                timeout=30.0,
            )

            if response.status_code == 200:
                data = response.json()
                if "data" in data and data["data"].get("allocateIpAddress"):
                    ip_data = data["data"]["allocateIpAddress"]["ipAddress"]
                    if ip_data:
                        allocated_ips.append(ip_data.get("address", "shared_v4"))
        except Exception:
            # Continue even if IPv4 allocation fails
            pass

        # Allocate IPv6 (always free)
        try:
            response = httpx.post(
                self.FLY_GRAPHQL_URL,
                headers={
                    "Authorization": f"Bearer {self.api_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "query": ipv4_mutation,
                    "variables": {
                        "input": {
                            "appId": app_name,
                            "type": "v6",
                        }
                    },
                },
                timeout=30.0,
            )

            if response.status_code == 200:
                data = response.json()
                if "data" in data and data["data"].get("allocateIpAddress"):
                    ip_data = data["data"]["allocateIpAddress"]["ipAddress"]
                    if ip_data:
                        allocated_ips.append(ip_data.get("address", "v6"))
        except Exception:
            # Continue even if IPv6 allocation fails
            pass

        return allocated_ips

    def _get_app(self, app_name: str) -> dict[str, Any] | None:
        """Get app details.

        Args:
            app_name: App name

        Returns:
            App details or None if not found
        """
        try:
            response = self._request("GET", f"/apps/{app_name}")
            return response.json()
        except FlyError as e:
            if e.status_code == 404:
                return None
            raise

    def _list_volumes(self, app_name: str) -> list[dict[str, Any]]:
        """List existing volumes for an app.

        Args:
            app_name: App name

        Returns:
            List of volume details
        """
        try:
            response = self._request("GET", f"/apps/{app_name}/volumes")
            return response.json()
        except FlyError as e:
            if e.status_code == 404:
                return []
            raise

    def _get_or_create_volume(
        self,
        app_name: str,
        name: str,
        region: str,
        size_gb: int,
    ) -> str:
        """Get existing volume or create new one (idempotent).

        Checks if a volume with the given name exists in the region.
        If so, returns its ID. Otherwise, creates a new volume.

        Args:
            app_name: App name
            name: Volume name
            region: Fly region (e.g., "iad")
            size_gb: Volume size in GB

        Returns:
            Volume ID
        """
        # Check if volume already exists in the region
        volumes = self._list_volumes(app_name)
        for vol in volumes:
            if vol.get("name") == name and vol.get("region") == region:
                return vol["id"]

        # Create new volume
        response = self._request(
            "POST",
            f"/apps/{app_name}/volumes",
            json={
                "name": name,
                "region": region,
                "size_gb": size_gb,
            },
        )
        return response.json()["id"]

    def _create_machine(
        self,
        app_name: str,
        config: MachineConfig,
    ) -> dict[str, Any]:
        """Create a Fly Machine.

        Args:
            app_name: App name
            config: Machine configuration

        Returns:
            Machine details
        """
        # Build service config with auto-stop if enabled
        service_config: dict[str, Any] = {
            "ports": [
                {
                    "port": 443,
                    "handlers": ["tls", "http"],
                },
                {
                    "port": 80,
                    "handlers": ["http"],
                },
            ],
            "protocol": "tcp",
            "internal_port": config.internal_port,
        }

        # Enable auto-stop for cost savings (machine stops when no traffic)
        if config.auto_stop:
            service_config["autostart"] = True
            service_config["autostop"] = "stop"
            service_config["min_machines_running"] = 0

        # Create/get volumes and build mounts list
        mounts = []
        for vol_config in config.volumes:
            volume_id = self._get_or_create_volume(
                app_name,
                vol_config.name,
                config.region,
                vol_config.size_gb,
            )
            mounts.append(
                {
                    "volume": volume_id,
                    "path": vol_config.path,
                }
            )

        machine_config: dict[str, Any] = {
            "image": config.image,
            "env": config.env,
            "services": [service_config],
            "checks": {
                "health": {
                    "type": "http",
                    "port": config.internal_port,
                    "path": config.health_check_path,
                    "interval": "30s",
                    "timeout": "20s",
                    "grace_period": "120s",
                },
            },
            "guest": {
                "cpu_kind": config.cpu_kind,
                "cpus": config.cpus,
                "memory_mb": config.memory_mb,
            },
        }

        # Add volume mounts if configured
        if mounts:
            machine_config["mounts"] = mounts

        # Add auto-stop timeout if enabled
        if config.auto_stop:
            machine_config["auto_destroy"] = False  # Don't destroy, just stop
            # Note: The actual idle timeout is managed by Fly's proxy, not machine config

        response = self._request(
            "POST",
            f"/apps/{app_name}/machines",
            json={
                "config": machine_config,
                "region": config.region,
            },
            timeout=120.0,  # Machine creation can take a while
        )
        return response.json()

    def _get_machine(self, app_name: str, machine_id: str) -> dict[str, Any]:
        """Get machine details.

        Args:
            app_name: App name
            machine_id: Machine ID

        Returns:
            Machine details
        """
        response = self._request("GET", f"/apps/{app_name}/machines/{machine_id}")
        return response.json()

    def _wait_for_machine(
        self,
        app_name: str,
        machine_id: str,
        timeout_seconds: int = 120,
    ) -> bool:
        """Wait for machine to be running.

        Args:
            app_name: App name
            machine_id: Machine ID
            timeout_seconds: Maximum wait time

        Returns:
            True if machine is running
        """
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            machine = self._get_machine(app_name, machine_id)
            state = machine.get("state", "")

            if state == "started":
                return True
            elif state in ("failed", "destroyed"):
                return False

            time.sleep(2)

        return False

    def _update_machine(
        self,
        app_name: str,
        machine_id: str,
        config: MachineConfig,
    ) -> dict[str, Any]:
        """Update a Fly Machine with a new image/config.

        Args:
            app_name: App name
            machine_id: Machine ID
            config: New machine configuration

        Returns:
            Updated machine details
        """
        # Build service config with auto-stop if enabled
        service_config: dict[str, Any] = {
            "ports": [
                {
                    "port": 443,
                    "handlers": ["tls", "http"],
                },
                {
                    "port": 80,
                    "handlers": ["http"],
                },
            ],
            "protocol": "tcp",
            "internal_port": config.internal_port,
        }

        # Enable auto-stop for cost savings (machine stops when no traffic)
        if config.auto_stop:
            service_config["autostart"] = True
            service_config["autostop"] = "stop"
            service_config["min_machines_running"] = 0

        # Create/get volumes and build mounts list
        mounts = []
        for vol_config in config.volumes:
            volume_id = self._get_or_create_volume(
                app_name,
                vol_config.name,
                config.region,
                vol_config.size_gb,
            )
            mounts.append(
                {
                    "volume": volume_id,
                    "path": vol_config.path,
                }
            )

        machine_config: dict[str, Any] = {
            "image": config.image,
            "env": config.env,
            "services": [service_config],
            "checks": {
                "health": {
                    "type": "http",
                    "port": config.internal_port,
                    "path": config.health_check_path,
                    "interval": "30s",
                    "timeout": "20s",
                    "grace_period": "120s",
                },
            },
            "guest": {
                "cpu_kind": config.cpu_kind,
                "cpus": config.cpus,
                "memory_mb": config.memory_mb,
            },
        }

        # Add volume mounts if configured
        if mounts:
            machine_config["mounts"] = mounts

        # Add auto-stop timeout if enabled
        if config.auto_stop:
            machine_config["auto_destroy"] = False  # Don't destroy, just stop

        response = self._request(
            "POST",
            f"/apps/{app_name}/machines/{machine_id}",
            json={
                "config": machine_config,
            },
            timeout=120.0,
        )
        return response.json()

    def _list_machines(self, app_name: str) -> list[dict[str, Any]]:
        """List all machines in an app.

        Args:
            app_name: App name

        Returns:
            List of machine details
        """
        response = self._request("GET", f"/apps/{app_name}/machines")
        return response.json()

    def deploy(
        self,
        deployment_id: str,
        config: MachineConfig,
    ) -> DeployResult:
        """Deploy a container to Fly.io.

        Args:
            deployment_id: Unique deployment identifier
            config: Machine configuration

        Returns:
            DeployResult with provider resource mapping
        """
        logs_buffer = []

        try:
            # Generate app name (must be unique across Fly)
            # Fly app names: lowercase letters, numbers, dashes only (no underscores)
            app_name = f"runtm-{deployment_id[:12]}".replace("_", "-")
            logs_buffer.append(f"Creating Fly app: {app_name}")

            # Check if app exists, create if not
            existing_app = self._get_app(app_name)
            if not existing_app:
                self._create_app(app_name)
                logs_buffer.append(f"Created app: {app_name}")

                # Allocate IP addresses for public access
                logs_buffer.append("Allocating IP addresses...")
                allocated_ips = self._allocate_ip_addresses(app_name)
                if allocated_ips:
                    logs_buffer.append(f"Allocated IPs: {', '.join(allocated_ips)}")
                else:
                    logs_buffer.append("Warning: Could not allocate IP addresses")
            else:
                logs_buffer.append(f"Using existing app: {app_name}")

            # Create machine
            logs_buffer.append(f"Creating machine with image: {config.image}")
            machine = self._create_machine(app_name, config)
            machine_id = machine["id"]
            region = machine.get("region", config.region)
            logs_buffer.append(f"Created machine: {machine_id} in {region}")

            # Wait for machine to start
            logs_buffer.append("Waiting for machine to start...")
            if not self._wait_for_machine(app_name, machine_id):
                logs_buffer.append("Machine failed to start")
                return DeployResult(
                    success=False,
                    error="Machine failed to start within timeout",
                    logs="\n".join(logs_buffer),
                )

            logs_buffer.append("Machine is running")

            # Build URL (uses custom domain if configured)
            url = construct_deployment_url(app_name)
            logs_buffer.append(f"Deployed to: {url}")

            return DeployResult(
                success=True,
                resource=ProviderResource(
                    app_name=app_name,
                    machine_id=machine_id,
                    region=region,
                    image_ref=config.image,
                    url=url,
                ),
                logs="\n".join(logs_buffer),
            )

        except FlyError as e:
            logs_buffer.append(f"Fly.io error: {e.message}")
            return DeployResult(
                success=False,
                error=e.message,
                logs="\n".join(logs_buffer),
            )
        except Exception as e:
            logs_buffer.append(f"Unexpected error: {e}")
            return DeployResult(
                success=False,
                error=str(e),
                logs="\n".join(logs_buffer),
            )

    def redeploy(
        self,
        resource: ProviderResource,
        config: MachineConfig,
    ) -> DeployResult:
        """Redeploy an existing machine with a new image.

        Updates the existing Fly machine rather than creating a new app.

        Args:
            resource: Existing provider resource to update
            config: New machine configuration (with new image)

        Returns:
            DeployResult with updated provider resource
        """
        logs_buffer = []

        try:
            app_name = resource.app_name
            machine_id = resource.machine_id
            logs_buffer.append(f"Redeploying to existing app: {app_name}")
            logs_buffer.append(f"Updating machine: {machine_id}")

            # Check if app still exists
            existing_app = self._get_app(app_name)
            if not existing_app:
                logs_buffer.append(f"App {app_name} not found, falling back to new deploy")
                # App was deleted, need to recreate
                return self.deploy(resource.app_name.replace("runtm-", ""), config)

            # Check if machine exists
            try:
                machine = self._get_machine(app_name, machine_id)
                machine_state = machine.get("state", "unknown")
                logs_buffer.append(f"Current machine state: {machine_state}")
            except FlyError as e:
                if e.status_code == 404:
                    logs_buffer.append(f"Machine {machine_id} not found, creating new machine")
                    # Machine was deleted, create new one
                    machine = self._create_machine(app_name, config)
                    machine_id = machine["id"]
                    logs_buffer.append(f"Created new machine: {machine_id}")
                else:
                    raise

            # Update the machine with new config
            logs_buffer.append(f"Updating machine with new image: {config.image}")
            updated_machine = self._update_machine(app_name, machine_id, config)
            new_machine_id = updated_machine.get("id", machine_id)
            region = updated_machine.get("region", resource.region)
            logs_buffer.append(f"Machine updated: {new_machine_id}")

            # Wait for machine to be running
            logs_buffer.append("Waiting for machine to start...")
            if not self._wait_for_machine(app_name, new_machine_id):
                logs_buffer.append("Machine failed to start")
                return DeployResult(
                    success=False,
                    error="Machine failed to start within timeout",
                    logs="\n".join(logs_buffer),
                )

            logs_buffer.append("Machine is running")

            # URL stays the same (uses custom domain if configured)
            url = construct_deployment_url(app_name)
            logs_buffer.append(f"Redeployed to: {url}")

            return DeployResult(
                success=True,
                resource=ProviderResource(
                    app_name=app_name,
                    machine_id=new_machine_id,
                    region=region,
                    image_ref=config.image,
                    url=url,
                ),
                logs="\n".join(logs_buffer),
            )

        except FlyError as e:
            logs_buffer.append(f"Fly.io error: {e.message}")
            return DeployResult(
                success=False,
                error=e.message,
                logs="\n".join(logs_buffer),
            )
        except Exception as e:
            logs_buffer.append(f"Unexpected error: {e}")
            return DeployResult(
                success=False,
                error=str(e),
                logs="\n".join(logs_buffer),
            )

    def get_status(self, resource: ProviderResource) -> ProviderStatus:
        """Get status of a deployed machine.

        Args:
            resource: Provider resource

        Returns:
            Current status of the machine
        """
        try:
            machine = self._get_machine(resource.app_name, resource.machine_id)
            state = machine.get("state", "unknown")

            # Check health from machine checks
            checks = machine.get("checks", [])
            healthy = any(check.get("status") == "passing" for check in checks)

            return ProviderStatus(
                state=state,
                healthy=healthy,
                url=resource.url,
            )
        except FlyError as e:
            return ProviderStatus(
                state="error",
                healthy=False,
                error=e.message,
            )

    def destroy(self, resource: ProviderResource) -> bool:
        """Destroy a deployed machine and its app.

        Args:
            resource: Provider resource

        Returns:
            True if successfully destroyed
        """
        try:
            # First, stop the machine (required before deletion)
            try:
                httpx.post(
                    f"{self.FLY_API_BASE}/apps/{resource.app_name}/machines/{resource.machine_id}/stop",
                    headers=self._headers(),
                    timeout=30.0,
                )
                # Wait briefly for machine to stop
                time.sleep(2)
            except Exception:
                pass  # Machine might already be stopped

            # Then destroy the machine with force
            self._request(
                "DELETE",
                f"/apps/{resource.app_name}/machines/{resource.machine_id}?force=true",
            )
        except FlyError:
            # Machine might already be gone, continue to delete app
            pass

        try:
            # Finally, delete the entire app
            # Note: This returns 202 Accepted, not 200
            response = httpx.delete(
                f"{self.FLY_API_BASE}/apps/{resource.app_name}",
                headers=self._headers(),
                timeout=30.0,
            )
            # Accept both 200 and 202 as success
            return response.status_code in (200, 202)
        except Exception:
            return False

    def get_logs(
        self,
        resource: ProviderResource,
        lines: int = 100,
    ) -> str:
        """Get runtime logs from a machine using flyctl.

        Uses the flyctl CLI which handles NATS/WireGuard internally.

        Args:
            resource: Provider resource
            lines: Number of lines to retrieve

        Returns:
            Log content as string
        """
        import re
        import subprocess

        try:
            # Use flyctl to fetch logs (handles NATS/auth internally)
            env = os.environ.copy()
            env["FLY_API_TOKEN"] = self.api_token

            result = subprocess.run(
                ["fly", "logs", "-a", resource.app_name, "--no-tail"],
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )

            if result.returncode != 0:
                error_msg = result.stderr.strip() if result.stderr else "Unknown error"
                return f"Failed to fetch logs: {error_msg}"

            log_output = result.stdout.strip()

            if not log_output:
                return "No runtime logs available yet."

            # Strip ANSI color codes for clean output
            ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
            log_output = ansi_escape.sub("", log_output)

            # Limit to last N lines if specified
            log_lines = log_output.split("\n")
            if lines and len(log_lines) > lines:
                log_lines = log_lines[-lines:]

            return "\n".join(log_lines)

        except subprocess.TimeoutExpired:
            return "Timeout fetching logs. The app may still be starting."
        except FileNotFoundError:
            return "flyctl not installed. Runtime logs unavailable."
        except Exception as e:
            return f"Error fetching logs: {str(e)}"

    def _graphql_request(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make a GraphQL API request.

        Args:
            query: GraphQL query or mutation
            variables: Query variables

        Returns:
            Response data

        Raises:
            FlyError: If request fails
        """
        try:
            response = httpx.post(
                self.FLY_GRAPHQL_URL,
                headers={
                    "Authorization": f"Bearer {self.api_token}",
                    "Content-Type": "application/json",
                },
                json={
                    "query": query,
                    "variables": variables or {},
                },
                timeout=30.0,
            )

            if response.status_code != 200:
                raise FlyError(
                    f"GraphQL request failed: {response.text}",
                    status_code=response.status_code,
                )

            data = response.json()
            if "errors" in data and data["errors"]:
                error_msg = data["errors"][0].get("message", "Unknown GraphQL error")
                raise FlyError(error_msg)

            return data.get("data", {})
        except httpx.RequestError as e:
            raise FlyError(f"GraphQL request failed: {e}") from e

    def _get_app_ips(self, app_name: str) -> list[dict[str, str]]:
        """Get IP addresses allocated to an app.

        Args:
            app_name: Fly app name

        Returns:
            List of IP address info dicts with 'address' and 'type' keys
        """
        query = """
        query($appName: String!) {
            app(name: $appName) {
                ipAddresses {
                    nodes {
                        address
                        type
                    }
                }
                sharedIpAddress
            }
        }
        """

        try:
            data = self._graphql_request(query, {"appName": app_name})
            app_data = data.get("app", {})
            nodes = app_data.get("ipAddresses", {}).get("nodes", [])

            # Shared IPv4 is returned separately, not in ipAddresses.nodes
            shared_ipv4 = app_data.get("sharedIpAddress")
            if shared_ipv4:
                nodes.append(
                    {
                        "address": shared_ipv4,
                        "type": "shared_v4",
                    }
                )

            return nodes
        except FlyError:
            return []

    def add_custom_domain(
        self,
        resource: ProviderResource,
        hostname: str,
    ) -> CustomDomainInfo:
        """Add a custom domain to a Fly.io app.

        Creates a certificate for the hostname using Fly's GraphQL API.
        Returns DNS records that need to be configured.

        Args:
            resource: Provider resource (Fly app)
            hostname: Custom domain (e.g., "api.example.com")

        Returns:
            CustomDomainInfo with DNS records and certificate status
        """
        app_name = resource.app_name

        # First, get the app's IP addresses for DNS records
        ips = self._get_app_ips(app_name)

        # Check if we have IPv4 - if not, try to allocate it
        has_ipv4 = any(ip.get("type", "").lower() in ("v4", "shared_v4") for ip in ips)

        if not has_ipv4:
            # Try to allocate shared IPv4 (FREE) if missing (required for many DNS providers like Squarespace)
            # Note: shared_v4 is free, dedicated v4 costs $2/month
            try:
                ipv4_mutation = """
                mutation($input: AllocateIPAddressInput!) {
                    allocateIpAddress(input: $input) {
                        ipAddress {
                            id
                            address
                            type
                        }
                    }
                }
                """
                result = self._graphql_request(
                    ipv4_mutation,
                    {
                        "input": {
                            "appId": app_name,
                            "type": "shared_v4",  # FREE shared IPv4, not dedicated ($2/mo)
                        }
                    },
                )
                # Check if allocation succeeded
                ip_data = result.get("allocateIpAddress", {}).get("ipAddress")
                if ip_data:
                    # Brief wait for IP to propagate, then refresh
                    import time

                    time.sleep(1)
                    ips = self._get_app_ips(app_name)
            except FlyError:
                # If allocation fails, continue with what we have
                # User will see a warning about IPv6-only with manual allocation instructions
                pass
            except Exception:
                # Other errors - continue with what we have
                pass

        dns_records = []
        record_name = "@" if hostname.count(".") == 1 else hostname.split(".")[0]

        # Prioritize IPv4 (A records) - many DNS providers require them
        # Sort: A records first, then AAAA, then CNAME
        for ip in ips:
            ip_type = ip.get("type", "").lower()
            address = ip.get("address", "")
            if ip_type == "v4" or ip_type == "shared_v4":
                dns_records.append(
                    DnsRecord(
                        record_type="A",
                        name=record_name,
                        value=address,
                    )
                )
            elif ip_type == "v6":
                dns_records.append(
                    DnsRecord(
                        record_type="AAAA",
                        name=record_name,
                        value=address,
                    )
                )

        # Check if certificate already exists (idempotency)
        check_query = """
        query($appName: String!, $hostname: String!) {
            app(name: $appName) {
                certificate(hostname: $hostname) {
                    id
                    hostname
                    configured
                    clientStatus
                    issued {
                        nodes {
                            type
                            expiresAt
                        }
                    }
                    dnsValidationHostname
                    dnsValidationTarget
                }
            }
        }
        """

        cert = None
        try:
            check_data = self._graphql_request(
                check_query,
                {
                    "appName": app_name,
                    "hostname": hostname,
                },
            )
            cert = check_data.get("app", {}).get("certificate")
        except FlyError:
            # Certificate doesn't exist yet, continue to create it
            pass

        # If certificate exists, return its status (idempotent)
        if cert:
            configured = cert.get("configured", False)
            issued = cert.get("issued", {}).get("nodes", [])

            if issued:
                cert_status = "issued"
            elif configured:
                cert_status = "pending"
            else:
                cert_status = "awaiting_dns"

            # Add CNAME for ACME validation if still needed
            dns_validation_hostname = cert.get("dnsValidationHostname")
            dns_validation_target = cert.get("dnsValidationTarget")
            if dns_validation_hostname and dns_validation_target and not configured:
                dns_records.append(
                    DnsRecord(
                        record_type="CNAME",
                        name=dns_validation_hostname,
                        value=dns_validation_target,
                    )
                )

            return CustomDomainInfo(
                hostname=hostname,
                configured=configured,
                certificate_status=cert_status,
                dns_records=dns_records,
                check_url=f"https://{hostname}",
            )

        # Create certificate for the hostname (only if it doesn't exist)
        mutation = """
        mutation($appId: ID!, $hostname: String!) {
            addCertificate(appId: $appId, hostname: $hostname) {
                certificate {
                    id
                    hostname
                    configured
                    clientStatus
                    issued {
                        nodes {
                            type
                            expiresAt
                        }
                    }
                    dnsValidationHostname
                    dnsValidationTarget
                }
            }
        }
        """

        try:
            data = self._graphql_request(
                mutation,
                {
                    "appId": app_name,
                    "hostname": hostname,
                },
            )

            cert = data.get("addCertificate", {}).get("certificate", {})
            configured = cert.get("configured", False)
            cert.get("clientStatus", "Awaiting configuration")
            issued = cert.get("issued", {}).get("nodes", [])

            # Determine certificate status
            if issued:
                cert_status = "issued"
            elif configured:
                cert_status = "pending"
            else:
                cert_status = "awaiting_dns"

            # Add CNAME for ACME validation if provided
            dns_validation_hostname = cert.get("dnsValidationHostname")
            dns_validation_target = cert.get("dnsValidationTarget")
            if dns_validation_hostname and dns_validation_target:
                dns_records.append(
                    DnsRecord(
                        record_type="CNAME",
                        name=dns_validation_hostname,
                        value=dns_validation_target,
                    )
                )

            return CustomDomainInfo(
                hostname=hostname,
                configured=configured,
                certificate_status=cert_status,
                dns_records=dns_records,
                check_url=f"https://{hostname}",
            )

        except FlyError as e:
            # If error says hostname already exists, treat as idempotent success
            if "already exists" in e.message.lower() or "duplicate" in e.message.lower():
                # Fetch existing certificate status
                try:
                    check_data = self._graphql_request(
                        check_query,
                        {
                            "appName": app_name,
                            "hostname": hostname,
                        },
                    )
                    cert = check_data.get("app", {}).get("certificate")
                    if cert:
                        configured = cert.get("configured", False)
                        issued = cert.get("issued", {}).get("nodes", [])

                        if issued:
                            cert_status = "issued"
                        elif configured:
                            cert_status = "pending"
                        else:
                            cert_status = "awaiting_dns"

                        dns_validation_hostname = cert.get("dnsValidationHostname")
                        dns_validation_target = cert.get("dnsValidationTarget")
                        if dns_validation_hostname and dns_validation_target and not configured:
                            dns_records.append(
                                DnsRecord(
                                    record_type="CNAME",
                                    name=dns_validation_hostname,
                                    value=dns_validation_target,
                                )
                            )

                        return CustomDomainInfo(
                            hostname=hostname,
                            configured=configured,
                            certificate_status=cert_status,
                            dns_records=dns_records,
                            check_url=f"https://{hostname}",
                        )
                except FlyError:
                    pass

            return CustomDomainInfo(
                hostname=hostname,
                configured=False,
                certificate_status="error",
                dns_records=dns_records,
                error=e.message,
            )

    def get_custom_domain_status(
        self,
        resource: ProviderResource,
        hostname: str,
    ) -> CustomDomainInfo:
        """Get status of a custom domain on a Fly.io app.

        Args:
            resource: Provider resource (Fly app)
            hostname: Custom domain to check

        Returns:
            CustomDomainInfo with current status
        """
        app_name = resource.app_name

        query = """
        query($appName: String!, $hostname: String!) {
            app(name: $appName) {
                certificate(hostname: $hostname) {
                    id
                    hostname
                    configured
                    clientStatus
                    issued {
                        nodes {
                            type
                            expiresAt
                        }
                    }
                    dnsValidationHostname
                    dnsValidationTarget
                }
            }
        }
        """

        try:
            data = self._graphql_request(
                query,
                {
                    "appName": app_name,
                    "hostname": hostname,
                },
            )

            app_data = data.get("app", {})
            cert = app_data.get("certificate")

            # Use _get_app_ips to get complete IP list (including shared IPv4)
            ips = self._get_app_ips(app_name)

            # Check if we have IPv4 - if not, try to allocate it
            has_ipv4 = any(ip.get("type", "").lower() in ("v4", "shared_v4") for ip in ips)

            if not has_ipv4:
                # Try to allocate shared IPv4 (FREE) if missing (required for many DNS providers)
                # Note: shared_v4 is free, dedicated v4 costs $2/month
                try:
                    ipv4_mutation = """
                    mutation($input: AllocateIPAddressInput!) {
                        allocateIpAddress(input: $input) {
                            ipAddress {
                                id
                                address
                                type
                            }
                        }
                    }
                    """
                    result = self._graphql_request(
                        ipv4_mutation,
                        {
                            "input": {
                                "appId": app_name,
                                "type": "shared_v4",  # FREE shared IPv4, not dedicated ($2/mo)
                            }
                        },
                    )
                    # Check if allocation succeeded
                    ip_data = result.get("allocateIpAddress", {}).get("ipAddress")
                    if ip_data:
                        # Brief wait for IP to propagate, then refresh
                        import time

                        time.sleep(1)
                        ips = self._get_app_ips(app_name)
                except FlyError:
                    # If allocation fails, continue with what we have
                    pass
                except Exception:
                    # Other errors - continue with what we have
                    pass

            # Build DNS records (prioritize IPv4)
            dns_records = []
            record_name = "@" if hostname.count(".") == 1 else hostname.split(".")[0]

            # Sort IPs: IPv4 first (v4, shared_v4), then IPv6
            def ip_sort_key(ip: dict[str, str]) -> int:
                ip_type = ip.get("type", "").lower()
                if ip_type in ("v4", "shared_v4"):
                    return 0  # IPv4 first
                elif ip_type == "v6":
                    return 1  # IPv6 second
                return 2  # Unknown types last

            sorted_ips = sorted(ips, key=ip_sort_key)

            for ip in sorted_ips:
                ip_type = ip.get("type", "").lower()
                address = ip.get("address", "")
                if ip_type == "v4" or ip_type == "shared_v4":
                    dns_records.append(
                        DnsRecord(
                            record_type="A",
                            name=record_name,
                            value=address,
                        )
                    )
                elif ip_type == "v6":
                    dns_records.append(
                        DnsRecord(
                            record_type="AAAA",
                            name=record_name,
                            value=address,
                        )
                    )

            if not cert:
                return CustomDomainInfo(
                    hostname=hostname,
                    configured=False,
                    certificate_status="not_found",
                    dns_records=dns_records,
                    error="Certificate not found. Run `runtm domain add` first.",
                )

            configured = cert.get("configured", False)
            issued = cert.get("issued", {}).get("nodes", [])

            if issued:
                cert_status = "issued"
            elif configured:
                cert_status = "pending"
            else:
                cert_status = "awaiting_dns"

            # Add validation CNAME if still needed
            dns_validation_hostname = cert.get("dnsValidationHostname")
            dns_validation_target = cert.get("dnsValidationTarget")
            if dns_validation_hostname and dns_validation_target and not configured:
                dns_records.append(
                    DnsRecord(
                        record_type="CNAME",
                        name=dns_validation_hostname,
                        value=dns_validation_target,
                    )
                )

            return CustomDomainInfo(
                hostname=hostname,
                configured=configured,
                certificate_status=cert_status,
                dns_records=dns_records,
                check_url=f"https://{hostname}",
            )

        except FlyError as e:
            return CustomDomainInfo(
                hostname=hostname,
                configured=False,
                certificate_status="error",
                error=e.message,
            )

    def remove_custom_domain(
        self,
        resource: ProviderResource,
        hostname: str,
    ) -> bool:
        """Remove a custom domain from a Fly.io app.

        Args:
            resource: Provider resource (Fly app)
            hostname: Custom domain to remove

        Returns:
            True if successfully removed
        """
        mutation = """
        mutation($appId: ID!, $hostname: String!) {
            deleteCertificate(appId: $appId, hostname: $hostname) {
                app {
                    name
                }
            }
        }
        """

        try:
            self._graphql_request(
                mutation,
                {
                    "appId": resource.app_name,
                    "hostname": hostname,
                },
            )
            return True
        except FlyError:
            return False
