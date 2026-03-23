"""Local Docker provider for development -- deploys to containers on the host."""

from __future__ import annotations

import logging
import time
from typing import Any

import docker
import httpx
from docker.errors import APIError, ImageNotFound, NotFound

from runtm_shared.types import CustomDomainInfo, MachineConfig, ProviderResource

from .base import DeployProvider, DeployResult, ProviderStatus

logger = logging.getLogger(__name__)

_PORT_RANGE_START = 9000
_PORT_RANGE_END = 9999
_NETWORK_NAME = "runtm-deploys"
_CONTAINER_PREFIX = "runtm-local-"


class LocalProvider(DeployProvider):
    """Deploys user apps as local Docker containers.

    This provider enables the full runtm workflow (init, deploy, logs, destroy)
    without any external accounts.  It is the default provider in the dev
    compose and is selected via ``DEPLOY_PROVIDER=local``.

    Requires access to the Docker daemon -- in the dev compose the worker
    service mounts ``/var/run/docker.sock``.
    """

    def __init__(self, base_url: str | None = None):
        self._client = docker.from_env() if base_url is None else docker.DockerClient(base_url=base_url)
        self._ensure_network()

    @property
    def name(self) -> str:
        return "local"

    # ── helpers ───────────────────────────────────────────────────────

    def _ensure_network(self) -> None:
        """Create the deploy network if it doesn't exist."""
        try:
            self._client.networks.get(_NETWORK_NAME)
        except NotFound:
            self._client.networks.create(_NETWORK_NAME, driver="bridge")

    def _container_name(self, deployment_id: str) -> str:
        safe = deployment_id.replace("_", "-")[:24]
        return f"{_CONTAINER_PREFIX}{safe}"

    def _pick_host_port(self) -> int:
        """Find the first unused port in the local deploy range."""
        used: set[int] = set()
        for c in self._client.containers.list(all=True):
            for _internal, bindings in (c.attrs.get("NetworkSettings", {}).get("Ports") or {}).items():
                for b in bindings or []:
                    try:
                        used.add(int(b["HostPort"]))
                    except (KeyError, ValueError, TypeError):
                        pass
        for port in range(_PORT_RANGE_START, _PORT_RANGE_END + 1):
            if port not in used:
                return port
        raise RuntimeError("No free ports in local deploy range")

    def _get_container(self, resource: ProviderResource) -> Any:
        cname = self._container_name(resource.app_name)
        return self._client.containers.get(cname)

    # ── DeployProvider interface ─────────────────────────────────────

    def deploy(
        self,
        deployment_id: str,
        config: MachineConfig,
    ) -> DeployResult:
        logs_buffer: list[str] = []
        try:
            cname = self._container_name(deployment_id)
            host_port = self._pick_host_port()
            logs_buffer.append(f"Local deploy: {cname} on port {host_port}")

            try:
                self._client.images.get(config.image)
            except ImageNotFound:
                logs_buffer.append(f"Pulling image {config.image} ...")
                self._client.images.pull(config.image)

            container = self._client.containers.run(
                config.image,
                name=cname,
                detach=True,
                ports={f"{config.internal_port}/tcp": host_port},
                environment=config.env,
                network=_NETWORK_NAME,
                labels={"runtm.deployment_id": deployment_id, "runtm.provider": "local"},
                mem_limit=f"{config.memory_mb}m",
            )

            logs_buffer.append(f"Container {container.short_id} started")

            # Brief wait then health-probe
            time.sleep(2)
            url = f"http://localhost:{host_port}"
            healthy = self._probe_health(url, config.health_check_path, timeout=30)
            if healthy:
                logs_buffer.append(f"Health check passed: {url}{config.health_check_path}")
            else:
                logs_buffer.append("Warning: health check did not pass within 30 s")

            resource = ProviderResource(
                app_name=deployment_id,
                machine_id=container.id,
                region="local",
                image_ref=config.image,
                url=url,
            )
            return DeployResult(success=True, resource=resource, logs="\n".join(logs_buffer))

        except Exception as e:
            logs_buffer.append(f"Error: {e}")
            return DeployResult(success=False, error=str(e), logs="\n".join(logs_buffer))

    def redeploy(
        self,
        resource: ProviderResource,
        config: MachineConfig,
    ) -> DeployResult:
        logs_buffer: list[str] = []
        try:
            cname = self._container_name(resource.app_name)
            logs_buffer.append(f"Redeploying {cname}")

            old_port: int | None = None
            try:
                old = self._client.containers.get(cname)
                # Grab the port before tearing down
                for _internal, bindings in (old.attrs.get("NetworkSettings", {}).get("Ports") or {}).items():
                    for b in bindings or []:
                        try:
                            old_port = int(b["HostPort"])
                        except (KeyError, ValueError, TypeError):
                            pass
                old.stop(timeout=5)
                old.remove(force=True)
                logs_buffer.append("Stopped previous container")
            except NotFound:
                logs_buffer.append("No existing container, deploying fresh")

            host_port = old_port or self._pick_host_port()

            try:
                self._client.images.get(config.image)
            except ImageNotFound:
                logs_buffer.append(f"Pulling image {config.image} ...")
                self._client.images.pull(config.image)

            container = self._client.containers.run(
                config.image,
                name=cname,
                detach=True,
                ports={f"{config.internal_port}/tcp": host_port},
                environment=config.env,
                network=_NETWORK_NAME,
                labels={"runtm.deployment_id": resource.app_name, "runtm.provider": "local"},
                mem_limit=f"{config.memory_mb}m",
            )
            logs_buffer.append(f"Container {container.short_id} started on port {host_port}")

            url = f"http://localhost:{host_port}"
            new_resource = ProviderResource(
                app_name=resource.app_name,
                machine_id=container.id,
                region="local",
                image_ref=config.image,
                url=url,
            )
            return DeployResult(success=True, resource=new_resource, logs="\n".join(logs_buffer))

        except Exception as e:
            logs_buffer.append(f"Error: {e}")
            return DeployResult(success=False, error=str(e), logs="\n".join(logs_buffer))

    def get_status(self, resource: ProviderResource) -> ProviderStatus:
        try:
            container = self._get_container(resource)
            container.reload()
            state = container.status  # running | exited | paused | ...
            healthy = state == "running"
            return ProviderStatus(state=state, healthy=healthy, url=resource.url)
        except NotFound:
            return ProviderStatus(state="stopped", healthy=False, error="Container not found")
        except APIError as e:
            return ProviderStatus(state="error", healthy=False, error=str(e))

    def destroy(self, resource: ProviderResource) -> bool:
        try:
            container = self._get_container(resource)
            container.stop(timeout=5)
            container.remove(force=True)
            return True
        except NotFound:
            return True  # already gone
        except APIError:
            return False

    def get_logs(self, resource: ProviderResource, lines: int = 100) -> str:
        try:
            container = self._get_container(resource)
            return container.logs(tail=lines).decode("utf-8", errors="replace")
        except NotFound:
            return "Container not found."
        except APIError as e:
            return f"Error fetching logs: {e}"

    def health_check(
        self,
        resource: ProviderResource,
        path: str = "/health",
        timeout_seconds: int = 30,
    ) -> bool:
        if not resource.url:
            return False
        return self._probe_health(resource.url, path, timeout_seconds)

    def add_custom_domain(
        self,
        resource: ProviderResource,
        hostname: str,
    ) -> CustomDomainInfo:
        return CustomDomainInfo(
            hostname=hostname,
            configured=False,
            certificate_status="not_supported",
            error="Custom domains are not supported by the local provider.",
        )

    def get_custom_domain_status(
        self,
        resource: ProviderResource,
        hostname: str,
    ) -> CustomDomainInfo:
        return CustomDomainInfo(
            hostname=hostname,
            configured=False,
            certificate_status="not_supported",
            error="Custom domains are not supported by the local provider.",
        )

    def remove_custom_domain(
        self,
        resource: ProviderResource,
        hostname: str,
    ) -> bool:
        return True  # nothing to remove

    # ── private ──────────────────────────────────────────────────────

    @staticmethod
    def _probe_health(base_url: str, path: str, timeout: int) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                r = httpx.get(f"{base_url}{path}", timeout=3)
                if r.status_code < 500:
                    return True
            except (httpx.RequestError, httpx.TimeoutException):
                pass
            time.sleep(1)
        return False
