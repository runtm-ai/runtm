"""Local filesystem artifact storage implementation."""

from __future__ import annotations

import shutil
from pathlib import Path

from runtm_shared.errors import ArtifactNotFoundError, StorageReadError, StorageWriteError
from runtm_shared.storage.base import ArtifactStore


class LocalFileStore(ArtifactStore):
    """Local filesystem implementation of ArtifactStore.

    Stores artifacts on a local filesystem path, typically a Docker volume
    shared between API and Worker containers in development.
    """

    def __init__(self, base_path: str):
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, key: str) -> Path:
        """Resolve storage key to filesystem path. Prevents directory traversal."""
        resolved = (self.base_path / key).resolve()
        if not str(resolved).startswith(str(self.base_path.resolve())):
            raise StorageWriteError(key, "Invalid key: path traversal detected")
        return resolved

    def put(self, key: str, data: bytes) -> str:
        path = self._resolve_path(key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return self.get_uri(key)
        except OSError as e:
            raise StorageWriteError(key, str(e)) from e

    def get(self, key: str) -> bytes:
        path = self._resolve_path(key)
        if not path.exists():
            raise ArtifactNotFoundError(key)
        try:
            return path.read_bytes()
        except OSError as e:
            raise StorageReadError(key, str(e)) from e

    def delete(self, key: str) -> None:
        path = self._resolve_path(key)
        try:
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
        except OSError as e:
            raise StorageWriteError(key, str(e)) from e

    def exists(self, key: str) -> bool:
        path = self._resolve_path(key)
        return path.exists()

    def get_uri(self, key: str) -> str:
        path = self._resolve_path(key)
        return f"file://{path}"

    def get_path(self, key: str) -> Path:
        """Get the filesystem path for an artifact."""
        return self._resolve_path(key)

    def get_size(self, key: str) -> int | None:
        path = self._resolve_path(key)
        if path.exists():
            return path.stat().st_size
        return None

    def put_file(self, key: str, file_path: str) -> str:
        """Store artifact from file path (optimized copy)."""
        dest_path = self._resolve_path(key)
        try:
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file_path, dest_path)
            return self.get_uri(key)
        except OSError as e:
            raise StorageWriteError(key, str(e)) from e

    def get_to_file(self, key: str, file_path: str) -> None:
        """Retrieve artifact to file path (optimized copy)."""
        src_path = self._resolve_path(key)
        if not src_path.exists():
            raise ArtifactNotFoundError(key)
        try:
            Path(file_path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, file_path)
        except OSError as e:
            raise StorageReadError(key, str(e)) from e
