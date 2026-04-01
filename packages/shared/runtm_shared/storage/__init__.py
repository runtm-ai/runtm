"""Storage interfaces, implementations, and extensible factory.

The OSS package ships with LocalFileStore (filesystem-backed).
Cloud extensions (e.g., runtm-cloud) can register additional backends
at startup via register_backend() without modifying this code.
"""

from __future__ import annotations

from typing import Callable

from runtm_shared.storage.base import ArtifactStore
from runtm_shared.storage.local import LocalFileStore

_backend_registry: dict[str, Callable[..., ArtifactStore]] = {}


def register_backend(name: str, factory: Callable[..., ArtifactStore]) -> None:
    """Register a storage backend factory.

    Called by cloud extensions at startup to plug in backends like S3/Tigris
    without modifying the OSS package.

    Args:
        name: Backend identifier (e.g. "s3")
        factory: Callable(**kwargs) -> ArtifactStore
    """
    _backend_registry[name] = factory


def get_artifact_store(backend: str = "local", **kwargs) -> ArtifactStore:
    """Factory that returns the configured ArtifactStore.

    Built-in backends:
        - "local": LocalFileStore (dev, shared Docker volume)

    Additional backends can be registered via register_backend().

    Args:
        backend: Backend name ("local", or any registered name)
        **kwargs: Backend-specific configuration forwarded to the factory

    Returns:
        Configured ArtifactStore instance

    Raises:
        ValueError: If backend is unknown
    """
    if backend == "local":
        return LocalFileStore(kwargs.get("storage_path", "/artifacts"))

    if backend in _backend_registry:
        return _backend_registry[backend](**kwargs)

    registered = ", ".join(sorted(_backend_registry)) if _backend_registry else ""
    available = f"local, {registered}" if registered else "local"
    raise ValueError(f"Unknown storage backend: '{backend}'. Available: {available}.")


__all__ = [
    "ArtifactStore",
    "LocalFileStore",
    "get_artifact_store",
    "register_backend",
]
