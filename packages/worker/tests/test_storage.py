"""Tests for worker storage shim (backwards compatibility).

LocalFileStore and factory tests now live in runtm_shared (packages/shared/tests/test_storage.py).
This file validates that the worker re-exports still work.
"""

from __future__ import annotations

import tempfile

from runtm_shared.storage import LocalFileStore as SharedLocalFileStore
from runtm_worker.storage import LocalFileStore, get_artifact_store


class TestWorkerStorageShim:
    """Verify re-exports from runtm_worker.storage still work."""

    def test_local_file_store_reexported(self) -> None:
        assert LocalFileStore is SharedLocalFileStore

    def test_get_artifact_store_returns_local(self) -> None:
        tmpdir = tempfile.mkdtemp()
        store = get_artifact_store(backend="local", storage_path=tmpdir)
        assert isinstance(store, LocalFileStore)
