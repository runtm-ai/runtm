"""Backwards-compat shim: re-exports from runtm_shared.storage.

The canonical home for LocalFileStore, get_artifact_store, and
register_backend is now runtm_shared.storage.  This module keeps
existing imports working (e.g. from runtm_worker.storage import ...).
"""

from runtm_shared.storage import (
    LocalFileStore,
    get_artifact_store,
    register_backend,
)

__all__ = ["LocalFileStore", "get_artifact_store", "register_backend"]
