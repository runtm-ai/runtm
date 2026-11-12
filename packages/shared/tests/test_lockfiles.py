"""Tests for lockfile detection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtm_shared import lockfiles
from runtm_shared.lockfiles import check_lockfile


@pytest.fixture
def node_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "package.json").write_text(json.dumps({"name": "app", "version": "1.0.0"}))
    # Keep the check offline: pretend the frozen install succeeds.
    monkeypatch.setattr(lockfiles, "_check_command_exists", lambda _cmd: True)
    monkeypatch.setattr(lockfiles, "_run_frozen_install", lambda _path, _cmd: True)
    return tmp_path


@pytest.mark.parametrize("name", ["bun.lock", "bun.lockb"])
def test_bun_lockfile_is_detected(node_project: Path, name: str) -> None:
    # Bun 1.2+ writes a text bun.lock by default; older versions write bun.lockb.
    (node_project / name).write_text("")
    status = check_lockfile(node_project, "node")
    assert status.exists
    assert status.manager == "bun"
    assert status.lockfile_path == name
    assert not status.needs_fix


def test_bun_text_lockfile_is_detected_in_fullstack_frontend(node_project: Path) -> None:
    frontend = node_project / "frontend"
    frontend.mkdir()
    (node_project / "package.json").rename(frontend / "package.json")
    (frontend / "bun.lock").write_text("")
    status = check_lockfile(node_project, "fullstack")
    assert status.lockfile_path == "frontend/bun.lock"
    assert not status.needs_fix


def test_package_lock_still_detected(node_project: Path) -> None:
    (node_project / "package-lock.json").write_text("{}")
    status = check_lockfile(node_project, "node")
    assert status.exists
    assert status.manager == "npm"
