"""Tests for shared storage: LocalFileStore, factory, and registry pattern.

These tests validate:
1. LocalFileStore works from its new home in runtm_shared.storage
2. get_artifact_store() factory defaults to LocalFileStore
3. register_backend() allows external packages (e.g., runtm-cloud) to
   plug in additional backends without modifying the OSS code
4. Unknown backends raise a clear ValueError
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from runtm_shared.errors import ArtifactNotFoundError, StorageWriteError
from runtm_shared.storage import (
    ArtifactStore,
    LocalFileStore,
    _backend_registry,
    get_artifact_store,
    register_backend,
)

# ---------------------------------------------------------------------------
# Fixture: clean registry between tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_registry():
    """Reset the backend registry before/after each test."""
    saved = dict(_backend_registry)
    _backend_registry.clear()
    yield
    _backend_registry.clear()
    _backend_registry.update(saved)


# ===================================================================
# LocalFileStore (imported from shared — must keep working)
# ===================================================================


class TestLocalFileStore:
    """Baseline LocalFileStore tests — identical behaviour after the move."""

    def setup_method(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.store = LocalFileStore(self.tmpdir)

    def test_put_and_get(self) -> None:
        data = b"hello artifact"
        self.store.put("dep_abc/artifact.zip", data)
        assert self.store.get("dep_abc/artifact.zip") == data

    def test_exists_true(self) -> None:
        self.store.put("dep_abc/artifact.zip", b"data")
        assert self.store.exists("dep_abc/artifact.zip") is True

    def test_exists_false(self) -> None:
        assert self.store.exists("nonexistent/artifact.zip") is False

    def test_get_missing_raises(self) -> None:
        with pytest.raises(ArtifactNotFoundError):
            self.store.get("nonexistent/artifact.zip")

    def test_delete(self) -> None:
        self.store.put("dep_abc/artifact.zip", b"data")
        self.store.delete("dep_abc/artifact.zip")
        assert self.store.exists("dep_abc/artifact.zip") is False

    def test_get_uri(self) -> None:
        uri = self.store.get_uri("dep_abc/artifact.zip")
        assert uri.startswith("file://")

    def test_get_path(self) -> None:
        self.store.put("dep_abc/artifact.zip", b"data")
        path = self.store.get_path("dep_abc/artifact.zip")
        assert isinstance(path, Path)
        assert path.exists()

    def test_get_size(self) -> None:
        self.store.put("dep_abc/artifact.zip", b"12345")
        assert self.store.get_size("dep_abc/artifact.zip") == 5

    def test_get_size_missing(self) -> None:
        assert self.store.get_size("nonexistent") is None

    def test_path_traversal_blocked(self) -> None:
        with pytest.raises(StorageWriteError):
            self.store.put("../../etc/passwd", b"evil")


# ===================================================================
# Factory — get_artifact_store()
# ===================================================================


class TestGetArtifactStore:
    """Tests for the registry-based factory."""

    def test_local_backend_returns_local_file_store(self) -> None:
        store = get_artifact_store(backend="local", storage_path="/tmp/test-artifacts")
        assert isinstance(store, LocalFileStore)

    def test_local_is_default_backend(self) -> None:
        store = get_artifact_store(storage_path="/tmp/test-artifacts")
        assert isinstance(store, LocalFileStore)

    def test_unknown_backend_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown storage backend"):
            get_artifact_store(backend="azure-blob")

    def test_unknown_backend_message_lists_available(self) -> None:
        register_backend("mock", lambda **_kw: None)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="mock"):
            get_artifact_store(backend="nope")


# ===================================================================
# Registry — register_backend()
# ===================================================================


class TestRegisterBackend:
    """Tests for the plugin registry that cloud extensions use."""

    def test_registered_backend_is_callable(self) -> None:
        """After register_backend('x', factory), get_artifact_store(backend='x') calls factory."""
        calls: list[dict] = []

        class FakeStore(ArtifactStore):
            def put(self, key, data):
                return f"fake://{key}"

            def get(self, key):
                return b""

            def delete(self, key):
                pass

            def exists(self, key):
                return False

            def get_uri(self, key):
                return f"fake://{key}"

        def fake_factory(**kwargs):
            calls.append(kwargs)
            return FakeStore()

        register_backend("fake", fake_factory)
        store = get_artifact_store(backend="fake", some_option="val")

        assert isinstance(store, FakeStore)
        assert len(calls) == 1
        assert calls[0]["some_option"] == "val"

    def test_register_overwrites_previous(self) -> None:
        """Registering the same name twice replaces the factory."""

        class StoreA(ArtifactStore):
            def put(self, key, data):
                return ""

            def get(self, key):
                return b""

            def delete(self, key):
                pass

            def exists(self, key):
                return False

            def get_uri(self, key):
                return ""

        class StoreB(ArtifactStore):
            def put(self, key, data):
                return ""

            def get(self, key):
                return b""

            def delete(self, key):
                pass

            def exists(self, key):
                return False

            def get_uri(self, key):
                return ""

        register_backend("x", lambda **_kw: StoreA())
        register_backend("x", lambda **_kw: StoreB())

        store = get_artifact_store(backend="x")
        assert isinstance(store, StoreB)

    def test_registered_backend_receives_kwargs(self) -> None:
        """The factory receives all **kwargs passed to get_artifact_store()."""
        received = {}

        class Dummy(ArtifactStore):
            def put(self, key, data):
                return ""

            def get(self, key):
                return b""

            def delete(self, key):
                pass

            def exists(self, key):
                return False

            def get_uri(self, key):
                return ""

        def factory(**kwargs):
            received.update(kwargs)
            return Dummy()

        register_backend("s3", factory)
        get_artifact_store(
            backend="s3",
            s3_bucket="my-bucket",
            s3_endpoint_url="https://example.com",
            s3_region="us-east-1",
        )

        assert received["s3_bucket"] == "my-bucket"
        assert received["s3_endpoint_url"] == "https://example.com"
        assert received["s3_region"] == "us-east-1"
