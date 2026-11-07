"""Root test configuration and fixtures."""

from __future__ import annotations

import os

# Set test environment defaults before any module imports Settings
os.environ.setdefault("DEBUG", "true")
os.environ.setdefault("RUNTM_API_SECRET", "test-secret-at-least-32-characters-long-key")
os.environ.setdefault("AUTH_MODE", "single_tenant")
os.environ.setdefault("ARTIFACT_STORAGE_PATH", "/tmp/artifacts")
