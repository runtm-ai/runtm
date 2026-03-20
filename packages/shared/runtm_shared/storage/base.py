"""Abstract storage interface for Runtm artifacts."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


class ArtifactStore(ABC):
    """Abstract interface for artifact storage.

    Built-in implementations:
        - LocalFileStore: Local filesystem (dev, shared Docker volume)

    Additional backends (e.g. S3/Tigris) can be registered via
    register_backend() from external packages like runtm-cloud.

    Usage:
        store = get_artifact_store(backend="local", storage_path="/artifacts")
        uri = store.put("artifacts/dep_abc123/artifact.zip", data)
        data = store.get("artifacts/dep_abc123/artifact.zip")
    """

    @abstractmethod
    def put(self, key: str, data: bytes) -> str:
        """Store artifact data.

        Args:
            key: Storage key (path-like string)
            data: Raw bytes to store

        Returns:
            URI of the stored artifact

        Raises:
            StorageWriteError: If write fails
        """
        ...

    @abstractmethod
    def get(self, key: str) -> bytes:
        """Retrieve artifact data.

        Args:
            key: Storage key (path-like string)

        Returns:
            Raw bytes of the artifact

        Raises:
            ArtifactNotFoundError: If artifact doesn't exist
            StorageReadError: If read fails
        """
        ...

    @abstractmethod
    def delete(self, key: str) -> None:
        """Delete artifact.

        Args:
            key: Storage key (path-like string)

        Raises:
            StorageWriteError: If delete fails
        """
        ...

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Check if artifact exists.

        Args:
            key: Storage key (path-like string)

        Returns:
            True if artifact exists, False otherwise
        """
        ...

    @abstractmethod
    def get_uri(self, key: str) -> str:
        """Get the URI for an artifact.

        Args:
            key: Storage key (path-like string)

        Returns:
            URI that can be used to reference the artifact
        """
        ...

    def put_file(self, key: str, file_path: str) -> str:
        """Store artifact from file path.

        Default implementation reads file and calls put().
        Subclasses may override for efficiency.

        Args:
            key: Storage key (path-like string)
            file_path: Path to file to store

        Returns:
            URI of the stored artifact
        """
        with open(file_path, "rb") as f:
            data = f.read()
        return self.put(key, data)

    def get_to_file(self, key: str, file_path: str) -> None:
        """Retrieve artifact to file path.

        Default implementation calls get() and writes to file.
        Subclasses may override for efficiency.

        Args:
            key: Storage key (path-like string)
            file_path: Path to write artifact to
        """
        data = self.get(key)
        with open(file_path, "wb") as f:
            f.write(data)

    def get_size(self, key: str) -> int | None:
        """Get the size of an artifact in bytes.

        Default implementation returns None (unknown).
        Subclasses may override for efficiency.

        Args:
            key: Storage key (path-like string)

        Returns:
            Size in bytes, or None if unknown/not exists
        """
        return None


def wait_for_artifact(
    storage,
    artifact_key: str,
    *,
    max_retries: int = 3,
    log_callback: Callable[[str], None] | None = None,
) -> Path:
    """Fetch artifact path with retry for S3 eventual consistency.

    Args:
        storage: ArtifactStore with a get_path(key) method
        artifact_key: Storage key for the artifact
        max_retries: Total attempts before giving up
        log_callback: Optional callable for logging retry messages

    Returns:
        Path to the artifact on local filesystem

    Raises:
        BuildError: If artifact not found after all retries
    """
    from runtm_shared.errors import BuildError

    for attempt in range(max_retries):
        artifact_path = storage.get_path(artifact_key)
        if artifact_path.exists():
            return artifact_path
        if attempt < max_retries - 1:
            wait = 2**attempt  # 1s, 2s
            if log_callback:
                log_callback(f"Artifact not available yet, retrying in {wait}s...")
            time.sleep(wait)

    raise BuildError(f"Artifact not found after {max_retries} attempts: {artifact_key}")


def compensate_failed_deploy(
    *,
    app_name: str | None,
    is_redeployment: bool,
    provider,
    dns_provider=None,
    base_domain: str | None = None,
) -> None:
    """Best-effort cleanup of partially provisioned infrastructure.

    Called from the deploy job's except block. Must never raise --
    all errors are logged and swallowed so the original failure
    propagates cleanly.

    Args:
        app_name: Fly app name (None if never assigned)
        is_redeployment: True if redeploying to existing infrastructure
        provider: FlyProvider instance (None if never created)
        dns_provider: DnsProvider instance (None if unconfigured)
        base_domain: Base domain for DNS cleanup
    """
    if not app_name or is_redeployment:
        return

    if provider:
        try:
            from runtm_shared.types import ProviderResource

            stub = ProviderResource(
                app_name=app_name,
                machine_id="",
                region="",
                image_ref="",
                url="",
            )
            provider.destroy(stub)
            logger.info("Rollback: destroyed Fly app %s", app_name)
        except Exception:
            logger.warning("Rollback: failed to destroy Fly app %s", app_name, exc_info=True)

    if dns_provider and base_domain:
        try:
            dns_provider.delete_record(subdomain=app_name, domain=base_domain)
            logger.info("Rollback: deleted DNS record for %s", app_name)
        except Exception:
            logger.warning("Rollback: failed to delete DNS record for %s", app_name, exc_info=True)
