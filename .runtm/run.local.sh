#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/common.sh"

"$PROJECT_ROOT/scripts/dev.sh" deps-up
"$PROJECT_ROOT/scripts/dev.sh" migrate

cd "$PROJECT_ROOT"
load_project_env

if [ ! -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    fail ".venv is missing. Run ./scripts/dev.sh setup first."
fi

# shellcheck disable=SC1091
source "$PROJECT_ROOT/.venv/bin/activate"

export RUNTM_API_SECRET="${RUNTM_API_SECRET:-dev-token-change-in-production}"
export RUNTM_API_KEY="${RUNTM_API_KEY:-$RUNTM_API_SECRET}"
export RUNTM_API_URL="${RUNTM_API_URL:-http://127.0.0.1:${RUNTM_API_PORT:-8000}}"
export DATABASE_URL="${DATABASE_URL:-postgresql://runtm:runtm@127.0.0.1:${RUNTM_POSTGRES_PORT:-5432}/runtm}"
export REDIS_URL="${REDIS_URL:-redis://127.0.0.1:${RUNTM_REDIS_PORT:-6379}}"
export ARTIFACT_STORAGE_PATH="${ARTIFACT_STORAGE_PATH:-$PROJECT_ROOT/artifacts}"
export ARTIFACT_STORAGE_BACKEND="${ARTIFACT_STORAGE_BACKEND:-local}"
export AUTH_MODE="${AUTH_MODE:-single_tenant}"
export DEBUG="${DEBUG:-true}"
export PYTHONUNBUFFERED=1

mkdir -p "$ARTIFACT_STORAGE_PATH"

api_port="${RUNTM_API_PORT:-8000}"
if port_serves_runtm_api "$api_port"; then
    fail "API port $api_port already has a Runtm API listening. Stop that process before starting run-local again."
fi

if ! port_is_available "$api_port"; then
    fail "API port $api_port is occupied by $(describe_port_owner "$api_port"). Run ./.runtm/setup.local.sh to allocate a free port window."
fi

api_pid=""
worker_pid=""
pid_file="$PROJECT_ROOT/.runtm/run.local.pids"

cleanup() {
    local pid

    for pid in "$api_pid" "$worker_pid"; do
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
        fi
    done

    for pid in "$api_pid" "$worker_pid"; do
        if [ -n "$pid" ]; then
            wait "$pid" 2>/dev/null || true
        fi
    done

    rm -f "$pid_file"
}

trap cleanup EXIT INT TERM

info "Starting API on ${RUNTM_API_URL}"
(
    cd "$PROJECT_ROOT/packages/api"
    exec python -m uvicorn runtm_api.main:app \
        --reload \
        --reload-dir "$PROJECT_ROOT/packages/api" \
        --reload-dir "$PROJECT_ROOT/packages/shared" \
        --host 127.0.0.1 \
        --port "${RUNTM_API_PORT:-8000}"
) &
api_pid="$!"

info "Starting worker against ${REDIS_URL}"
(
    cd "$PROJECT_ROOT/packages/worker"
    exec python -m runtm_worker.main
) &
worker_pid="$!"

mkdir -p "$PROJECT_ROOT/.runtm"
{
    printf 'api=%s\n' "$api_pid"
    printf 'worker=%s\n' "$worker_pid"
} > "$pid_file"

info "Local API and worker are running. Press Ctrl+C to stop."

while true; do
    if ! kill -0 "$api_pid" 2>/dev/null; then
        set +e
        wait "$api_pid"
        exit_code="$?"
        set -e
        exit "$exit_code"
    fi

    if ! kill -0 "$worker_pid" 2>/dev/null; then
        set +e
        wait "$worker_pid"
        exit_code="$?"
        set -e
        exit "$exit_code"
    fi

    sleep 1
done
