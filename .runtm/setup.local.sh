#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/common.sh"

REFRESH_DEPS=false

for arg in "$@"; do
    case "$arg" in
        --refresh-deps)
            REFRESH_DEPS=true
            ;;
        -h|--help)
            cat <<'EOF'
Usage: ./.runtm/setup.local.sh [--refresh-deps]

Creates a fully local Runtm development environment for this workspace:
  - copies .env.local.example to .env when needed
  - allocates a stable per-workspace port window
  - writes .runtm/ports.json and .runtm/config.local.json
  - installs local Python/sandbox dependencies when needed
  - starts Postgres/Redis with an isolated Docker Compose project
  - applies migrations from .venv
  - checks the API with a temporary host process and runs runtm-dev doctor
EOF
            exit 0
            ;;
        *)
            fail "Unknown option: $arg"
            ;;
    esac
done

check_dependencies() {
    command -v docker >/dev/null 2>&1 || fail "Docker is required"
    command -v curl >/dev/null 2>&1 || fail "curl is required"

    if ! docker info >/dev/null 2>&1; then
        fail "Docker is installed, but the Docker daemon is not running"
    fi

    if ! command -v uv >/dev/null 2>&1; then
        warn "uv is not installed; scripts/dev.sh setup will fall back to Python 3.11+"
    fi
}

ensure_env_file() {
    cd "$PROJECT_ROOT"

    if [ -f ".env" ]; then
        success "Keeping existing .env"
        return 0
    fi

    if [ -f ".env.local.example" ]; then
        cp ".env.local.example" ".env"
        success "Created .env from .env.local.example"
        return 0
    fi

    cp "infra/local.env.example" ".env"
    success "Created .env from infra/local.env.example"
}

configure_workspace() {
    local port_base api_port postgres_port redis_port api_secret api_key api_url

    load_project_env

    port_base="$(allocate_port_base)"
    api_port="$port_base"
    postgres_port="$((port_base + 1))"
    redis_port="$((port_base + 2))"
    api_secret="${RUNTM_API_SECRET:-dev-token-change-in-production}"
    api_key="${RUNTM_API_KEY:-$api_secret}"
    api_url="http://127.0.0.1:$api_port"

    write_env_var "RUNTM_API_SECRET" "$api_secret"
    write_env_var "RUNTM_WORKSPACE_NAME" "$(runtm_workspace_name)"
    write_env_var "RUNTM_DOCKER_PROJECT" "$(runtm_docker_project)"
    write_env_var "RUNTM_PORT_BASE" "$port_base"
    write_env_var "RUNTM_API_BIND" "127.0.0.1"
    write_env_var "RUNTM_API_PORT" "$api_port"
    write_env_var "RUNTM_POSTGRES_PORT" "$postgres_port"
    write_env_var "RUNTM_REDIS_PORT" "$redis_port"
    write_env_var "RUNTM_API_URL" "$api_url"
    write_env_var "RUNTM_API_KEY" "$api_key"
    write_env_var "DATABASE_URL" "postgresql://runtm:runtm@127.0.0.1:$postgres_port/runtm"
    write_env_var "REDIS_URL" "redis://127.0.0.1:$redis_port"
    write_env_var "ARTIFACT_STORAGE_PATH" "$PROJECT_ROOT/artifacts"

    write_ports_json "$port_base" "$api_port" "$postgres_port" "$redis_port" "$api_url"
    write_config_overlay

    success "Configured workspace $(runtm_workspace_name)"
    success "Port map: API=$api_url Postgres=127.0.0.1:$postgres_port Redis=127.0.0.1:$redis_port"
    success "Updated .env, .runtm/ports.json, and .runtm/config.local.json"
}

install_dependencies() {
    cd "$PROJECT_ROOT"

    if [ "$REFRESH_DEPS" = false ] && [ -x ".venv/bin/runtm-dev" ]; then
        success "Using existing .venv; pass --refresh-deps to reinstall"
        return 0
    fi

    "$PROJECT_ROOT/scripts/dev.sh" setup
}

start_services() {
    "$PROJECT_ROOT/scripts/dev.sh" deps-up
}

run_migrations() {
    "$PROJECT_ROOT/scripts/dev.sh" migrate
}

TEMP_API_PID=""

stop_temp_api() {
    if [ -n "$TEMP_API_PID" ] && kill -0 "$TEMP_API_PID" 2>/dev/null; then
        kill "$TEMP_API_PID" 2>/dev/null || true
        wait "$TEMP_API_PID" 2>/dev/null || true
    fi
}

start_api_for_doctor() {
    local api_url

    load_project_env
    api_url="${RUNTM_API_URL:-http://127.0.0.1:${RUNTM_API_PORT:-8000}}"

    if curl -fsS "$api_url/health" >/dev/null 2>&1; then
        success "API already running at $api_url"
        return 0
    fi

    if [ ! -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
        fail ".venv is missing. Run ./scripts/dev.sh setup first."
    fi

    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.venv/bin/activate"

    export RUNTM_API_SECRET="${RUNTM_API_SECRET:-dev-token-change-in-production}"
    export RUNTM_API_KEY="${RUNTM_API_KEY:-$RUNTM_API_SECRET}"
    export RUNTM_API_URL="$api_url"
    export DATABASE_URL="${DATABASE_URL:-postgresql://runtm:runtm@127.0.0.1:${RUNTM_POSTGRES_PORT:-5432}/runtm}"
    export REDIS_URL="${REDIS_URL:-redis://127.0.0.1:${RUNTM_REDIS_PORT:-6379}}"
    export ARTIFACT_STORAGE_PATH="${ARTIFACT_STORAGE_PATH:-$PROJECT_ROOT/artifacts}"
    export ARTIFACT_STORAGE_BACKEND="${ARTIFACT_STORAGE_BACKEND:-local}"
    export AUTH_MODE="${AUTH_MODE:-single_tenant}"
    export DEBUG="${DEBUG:-true}"
    export PYTHONUNBUFFERED=1

    mkdir -p "$ARTIFACT_STORAGE_PATH" "$PROJECT_ROOT/.runtm"

    (
        cd "$PROJECT_ROOT/packages/api"
        exec python -m uvicorn runtm_api.main:app \
            --host 127.0.0.1 \
            --port "${RUNTM_API_PORT:-8000}"
    ) > "$PROJECT_ROOT/.runtm/api.setup.log" 2>&1 &
    TEMP_API_PID="$!"
}

wait_for_api() {
    local api_url attempt

    load_project_env
    api_url="${RUNTM_API_URL:-http://127.0.0.1:${RUNTM_API_PORT:-8000}}"

    for attempt in $(seq 1 60); do
        if curl -fsS "$api_url/health" >/dev/null 2>&1; then
            success "API is healthy at $api_url"
            return 0
        fi
        if [ -n "$TEMP_API_PID" ] && ! kill -0 "$TEMP_API_PID" 2>/dev/null; then
            tail -n 40 "$PROJECT_ROOT/.runtm/api.setup.log" 2>/dev/null || true
            fail "Temporary API exited before becoming healthy"
        fi
        sleep 2
    done

    fail "API did not become healthy at $api_url"
}

run_doctor() {
    cd "$PROJECT_ROOT"
    load_project_env

    if [ ! -x ".venv/bin/runtm-dev" ]; then
        warn "Skipping runtm-dev doctor because .venv/bin/runtm-dev is missing"
        return 0
    fi

    RUNTM_API_URL="${RUNTM_API_URL:-http://127.0.0.1:${RUNTM_API_PORT:-8000}}" \
    RUNTM_API_KEY="${RUNTM_API_KEY:-dev-token-change-in-production}" \
        ".venv/bin/runtm-dev" doctor
}

print_summary() {
    load_project_env

    cat <<EOF

Runtm local development is ready.

Workspace: ${RUNTM_WORKSPACE_NAME:-$(runtm_workspace_name)}
Compose:   ${RUNTM_DOCKER_PROJECT:-$(runtm_docker_project)}
API:       ${RUNTM_API_URL:-http://127.0.0.1:${RUNTM_API_PORT:-8000}}
Postgres:  127.0.0.1:${RUNTM_POSTGRES_PORT:-5432}
Redis:     127.0.0.1:${RUNTM_REDIS_PORT:-6379}
Mode:      run-local uses host Python; up uses full Docker.

Useful commands:
  ./scripts/dev.sh run-local
  ./scripts/dev.sh deps-up
  ./scripts/dev.sh up          # full Docker stack
  ./scripts/dev.sh logs
  ./scripts/dev.sh doctor
  ./scripts/dev.sh doctor-local
  ./scripts/dev.sh diagnose-env
  ./scripts/dev.sh ports
  ./.runtm/teardown.local.sh

Port metadata:
  .runtm/ports.json
EOF
}

run_local_doctor() {
    cd "$PROJECT_ROOT"
    load_project_env
    local_dev_doctor
}

main() {
    trap stop_temp_api EXIT

    info "Checking local dependencies"
    check_dependencies

    info "Preparing environment"
    ensure_env_file

    info "Allocating workspace ports"
    configure_workspace

    info "Preparing Python and sandbox tooling"
    install_dependencies

    info "Starting local dependency services"
    start_services

    info "Applying database migrations"
    run_migrations

    info "Starting temporary API for setup verification"
    start_api_for_doctor

    info "Waiting for API health"
    wait_for_api

    info "Running development doctor"
    run_doctor

    info "Stopping temporary API"
    stop_temp_api
    TEMP_API_PID=""

    info "Validating local development metadata"
    run_local_doctor

    print_summary
}

main
