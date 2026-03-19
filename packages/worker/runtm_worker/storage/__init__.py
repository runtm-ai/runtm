"""Storage implementations."""

from __future__ import annotations

from runtm_shared.storage.base import ArtifactStore
from runtm_worker.storage.local import LocalFileStore


def get_artifact_store(
    backend: str = "local",
    storage_path: str = "/artifacts",
    s3_bucket: str = "",
    s3_endpoint_url: str | None = None,
    s3_region: str = "auto",
) -> ArtifactStore:
    """Factory that returns the right ArtifactStore for the environment.

    Args:
        backend: "local" (dev, shared Docker volume) or "s3" (production, Tigris)
        storage_path: Filesystem path for local backend
        s3_bucket: Bucket name for S3 backend
        s3_endpoint_url: Endpoint URL (e.g. https://fly.storage.tigris.dev)
        s3_region: AWS region (default "auto" for Tigris)

    Returns:
        Configured ArtifactStore instance

    Raises:
        ValueError: If backend is unknown or required config is missing
    """
    if backend == "local":
        return LocalFileStore(storage_path)

    if backend == "s3":
        if not s3_bucket:
            raise ValueError(
                "S3_BUCKET is required when ARTIFACT_STORAGE_BACKEND=s3. "
                "Run `fly storage create` to provision a Tigris bucket."
            )
        from runtm_worker.storage.s3 import S3FileStore

        return S3FileStore(
            bucket=s3_bucket,
            endpoint_url=s3_endpoint_url,
            region=s3_region,
        )

    raise ValueError(
        f"Unknown storage backend: '{backend}'. Use 'local' or 's3'."
    )


__all__ = ["LocalFileStore", "get_artifact_store"]
