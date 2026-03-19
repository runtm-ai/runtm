"""Abstract storage interface for Runtm artifacts."""

from __future__ import annotations

import tempfile
from abc import ABC, abstractmethod
from pathlib import Path


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

    def get_path(self, key: str) -> Path:
        """Get a local filesystem path for an artifact.

        For local backends this returns the real path. For remote backends
        (S3, etc.) this downloads to a temp directory. Callers should call
        cleanup_path() when done to avoid temp file leaks.

        Args:
            key: Storage key (path-like string)

        Returns:
            Path to the artifact on the local filesystem

        Raises:
            ArtifactNotFoundError: If artifact doesn't exist
            StorageReadError: If download fails
        """
        data = self.get(key)
        tmp_dir = Path(tempfile.mkdtemp(prefix="runtm-artifact-"))
        filename = key.rsplit("/", 1)[-1] if "/" in key else key
        dest = tmp_dir / filename
        dest.write_bytes(data)
        return dest

    def cleanup_path(self, path: Path) -> None:
        """Remove a temp directory created by get_path().

        Safe to call on non-temp paths (no-op for local backends).
        """
        import shutil

        if path.exists():
            parent = path.parent
            if parent.name.startswith("runtm-artifact-"):
                shutil.rmtree(parent, ignore_errors=True)

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
