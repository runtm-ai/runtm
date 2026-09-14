from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
COMMON_SH = REPO_ROOT / ".runtm" / "lib" / "common.sh"


def _can_bind(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _find_free_window() -> int:
    for base in range(25000, 42000, 50):
        ports = (
            base,
            base + 1,
            base + 2,
            base + 20,
            base + 21,
            base + 22,
        )
        if all(_can_bind(port) for port in ports):
            return base
    raise AssertionError("could not find free port windows for local dev script tests")


def _run_common_script(script: str, state_home: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["RUNTM_STATE_HOME"] = str(state_home)
    return subprocess.run(
        ["bash", "-lc", script],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )


def test_allocate_port_base_reuses_existing_workspace_window(tmp_path: Path) -> None:
    base = _find_free_window()
    script = f"""
        set -euo pipefail
        source "{COMMON_SH}"
        PORT_START={base}
        PORT_END={base + 42}
        first="$(allocate_port_base)"
        second="$(allocate_port_base)"
        printf '%s\\n%s\\n' "$first" "$second"
    """

    result = _run_common_script(script, tmp_path)

    assert result.stdout.splitlines() == [str(base), str(base)]
    assert "Allocated workspace port window" in result.stderr
    assert "Reusing workspace port window" in result.stderr


def test_allocate_port_base_skips_occupied_window(tmp_path: Path) -> None:
    base = _find_free_window()
    expected = base + 20

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", base))
        listener.listen(1)

        script = f"""
            set -euo pipefail
            source "{COMMON_SH}"
            PORT_START={base}
            PORT_END={base + 42}
            allocate_port_base
        """

        result = _run_common_script(script, tmp_path)

    assert result.stdout.strip() == str(expected)
    assert f"Allocated workspace port window {expected}" in result.stderr


def test_cleanup_port_allocations_drops_deleted_worktrees_and_invalid_ports(
    tmp_path: Path,
) -> None:
    base = _find_free_window()
    allocations_file = tmp_path / "port-allocations.tsv"
    missing_workspace = tmp_path / "deleted-worktree"
    current_workspace = REPO_ROOT.resolve()
    allocations_file.write_text(
        f"{missing_workspace}\t{base}\n"
        f"{current_workspace}\tnot-a-port\n"
        f"{current_workspace}\t{base + 20}\n",
        encoding="utf-8",
    )

    script = f"""
        set -euo pipefail
        source "{COMMON_SH}"
        PORT_START={base}
        PORT_END={base + 42}
        cleanup_port_allocations
        cat "$PORT_ALLOCATIONS_FILE"
    """

    result = _run_common_script(script, tmp_path)

    assert result.stdout == f"{current_workspace}\t{base + 20}\n"
