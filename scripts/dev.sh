#!/bin/bash
# Development helper script

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Load .env file if it exists (exports all variables)
load_env() {
    if [ -f "$PROJECT_ROOT/.env" ]; then
        set -a
        source "$PROJECT_ROOT/.env"
        set +a
        echo "✓ Loaded environment from .env"
    else
        echo "⚠ No .env file found. Run ./.runtm/setup.local.sh or copy .env.local.example to .env"
    fi
}

compose_cmd() {
    local compose_args=()

    if [ -n "${RUNTM_DOCKER_PROJECT:-}" ]; then
        compose_args=(-p "$RUNTM_DOCKER_PROJECT")
    fi

    docker compose "${compose_args[@]}" -f infra/docker-compose.yml --env-file .env "$@"
}

local_api_url() {
    if [ -n "${RUNTM_API_URL:-}" ]; then
        printf '%s\n' "$RUNTM_API_URL"
    else
        printf 'http://127.0.0.1:%s\n' "${RUNTM_API_PORT:-8000}"
    fi
}

activate_venv() {
    if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
        source "$PROJECT_ROOT/.venv/bin/activate"
    fi
}

wait_for_compose_service() {
    local service="$1"
    local attempts="${2:-60}"
    local container_id=""
    local status=""

    for _ in $(seq 1 "$attempts"); do
        container_id="$(compose_cmd ps -q "$service" 2>/dev/null || true)"
        if [ -n "$container_id" ]; then
            status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id" 2>/dev/null || true)"
            if [ "$status" = "healthy" ] || [ "$status" = "running" ]; then
                return 0
            fi
        fi
        sleep 1
    done

    echo "Timed out waiting for $service to become healthy/running"
    return 1
}

mask_value() {
    local key="$1"
    local value="$2"

    case "$key" in
        *TOKEN*|*SECRET*|*KEY*|*PASSWORD*)
            if [ -n "$value" ]; then
                printf '<set>'
            else
                printf '<empty>'
            fi
            ;;
        *)
            printf '%s' "$value"
            ;;
    esac
}

print_selected_env() {
    local key value

    for key in \
        RUNTM_WORKSPACE_NAME \
        RUNTM_DOCKER_PROJECT \
        RUNTM_PORT_BASE \
        RUNTM_API_BIND \
        RUNTM_API_PORT \
        RUNTM_POSTGRES_PORT \
        RUNTM_REDIS_PORT \
        RUNTM_API_URL \
        RUNTM_API_KEY \
        RUNTM_API_SECRET \
        DATABASE_URL \
        REDIS_URL \
        ARTIFACT_STORAGE_PATH \
        FLY_API_TOKEN \
        FLY_ORG
    do
        value="${!key:-}"
        printf '  %-24s %s\n' "$key" "$(mask_value "$key" "$value")"
    done
}

case "$1" in
    setup-local)
        "$PROJECT_ROOT/.runtm/setup.local.sh" "${@:2}"
        ;;

    teardown-local)
        "$PROJECT_ROOT/.runtm/teardown.local.sh" "${@:2}"
        ;;

    run-local)
        "$PROJECT_ROOT/.runtm/run.local.sh"
        ;;

    setup)
        echo "Setting up development environment..."
        cd "$PROJECT_ROOT"

        # 1. Create/activate virtual environment
        # The sandbox and agents packages require Python 3.11+. macOS still ships
        # /usr/bin/python3 as 3.9, so prefer uv-managed Python when available.
        if [ ! -d ".venv" ]; then
            echo "Creating virtual environment..."
            if command -v uv &> /dev/null; then
                uv venv --python 3.12 .venv
            else
                PYTHON_BIN=""
                for candidate in python3.12 python3.11 python3; do
                    if command -v "$candidate" &> /dev/null && "$candidate" - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
                    then
                        PYTHON_BIN="$candidate"
                        break
                    fi
                done

                if [ -z "$PYTHON_BIN" ]; then
                    echo "Python 3.11+ is required for local sandbox development."
                    echo "Install uv (recommended) or Python 3.11+, then rerun ./scripts/dev.sh setup."
                    exit 1
                fi

                "$PYTHON_BIN" -m venv .venv
            fi
        fi

        echo "Activating virtual environment..."
        source .venv/bin/activate

        if ! python - <<'PY'
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
        then
            echo ".venv is using Python $(python -V), but local sandbox development needs Python 3.11+."
            echo "Remove .venv and rerun ./scripts/dev.sh setup."
            exit 1
        fi

        # 2. Ensure pip is present, then upgrade it
        if ! python -m pip --version &> /dev/null; then
            python -m ensurepip --upgrade
        fi
        python -m pip install --upgrade pip

        # 3. Install Python packages in development mode
        echo ""
        echo "Installing Python packages..."
        python -m pip install -e "packages/shared[dev]"
        python -m pip install -e "packages/sandbox[dev]"
        python -m pip install -e "packages/agents[dev]"
        python -m pip install -e "packages/api[dev]"
        python -m pip install -e "packages/worker[dev]"
        python -m pip install -e "packages/cli[dev,sandbox]"

        # 4. Install pre-commit hooks
        python -m pip install pre-commit
        pre-commit install

        # 5. Install sandbox dependencies (bun, sandbox-runtime, claude)
        echo ""
        echo "Installing sandbox dependencies..."

        # Install Bun if not present
        if ! command -v bun &> /dev/null; then
            echo "  Installing Bun..."
            curl -fsSL https://bun.sh/install | bash
            export BUN_INSTALL="$HOME/.bun"
            export PATH="$BUN_INSTALL/bin:$PATH"
        else
            echo "  ✓ Bun already installed"
        fi

        # Install sandbox-runtime if not present
        if ! command -v srt &> /dev/null; then
            echo "  Installing sandbox-runtime..."
            if command -v bun &> /dev/null; then
                bun install -g @anthropic-ai/sandbox-runtime
            elif command -v npm &> /dev/null; then
                npm install -g @anthropic-ai/sandbox-runtime
            else
                echo "  ⚠ Could not install sandbox-runtime (no bun or npm)"
            fi
        else
            echo "  ✓ sandbox-runtime already installed"
        fi

        # Install Claude CLI if not present
        if ! command -v claude &> /dev/null; then
            echo "  Installing Claude Code CLI..."
            curl -fsSL https://claude.ai/install.sh | bash
        else
            echo "  ✓ Claude Code CLI already installed"
        fi

        echo ""
        echo "============================================"
        echo "Development environment ready!"
        echo ""
        echo "If this is your first time, reload your shell:"
        echo "  source ~/.zshrc   # or restart terminal"
        echo ""
        echo "Then run:"
        echo "  source .venv/bin/activate"
        echo "  runtm-dev start"
        echo "============================================"
        ;;

    up)
        echo "Starting full Docker services..."
        cd "$PROJECT_ROOT"
        load_env
        if ! compose_cmd --profile app up -d; then
            echo "Failed to start the full Docker stack. A configured port may be occupied."
            echo "Run ./.runtm/setup.local.sh to re-check ports and update .env/.runtm/ports.json."
            exit 1
        fi
        wait_for_compose_service postgres
        wait_for_compose_service redis
        wait_for_compose_service api
        echo "Full Docker stack started. API available at $(local_api_url)"
        echo "Postgres 127.0.0.1:${RUNTM_POSTGRES_PORT:-5432}, Redis 127.0.0.1:${RUNTM_REDIS_PORT:-6379}"
        ;;

    deps-up)
        echo "Starting local dependency services..."
        cd "$PROJECT_ROOT"
        load_env
        compose_cmd --profile app stop api worker >/dev/null 2>&1 || true
        if ! compose_cmd up -d postgres redis; then
            echo "Failed to start local dependencies. A configured port may be occupied."
            echo "Run ./.runtm/setup.local.sh to re-check ports and update .env/.runtm/ports.json."
            exit 1
        fi
        wait_for_compose_service postgres
        wait_for_compose_service redis
        echo "Dependencies started: Postgres 127.0.0.1:${RUNTM_POSTGRES_PORT:-5432}, Redis 127.0.0.1:${RUNTM_REDIS_PORT:-6379}"
        echo "API will run at $(local_api_url) when you start ./scripts/dev.sh run-local"
        echo "Run ./scripts/dev.sh run-local to start API + worker from .venv."
        ;;

    up-docker)
        "$0" up
        ;;

    down)
        echo "Stopping local services..."
        cd "$PROJECT_ROOT"
        load_env
        compose_cmd --profile app down
        ;;

    restart)
        echo "Restarting full Docker services..."
        cd "$PROJECT_ROOT"
        load_env
        compose_cmd --profile app down
        if ! compose_cmd --profile app up -d; then
            echo "Failed to restart the full Docker stack. Run ./.runtm/setup.local.sh to re-check ports."
            exit 1
        fi
        wait_for_compose_service postgres
        wait_for_compose_service redis
        wait_for_compose_service api
        echo "Full Docker stack restarted. API available at $(local_api_url)"
        ;;

    rebuild|rebuild-docker)
        echo "Rebuilding and restarting full Docker services..."
        cd "$PROJECT_ROOT"
        load_env
        compose_cmd --profile app build api worker
        if ! compose_cmd --profile app up -d; then
            echo "Failed to start rebuilt Docker services. Run ./.runtm/setup.local.sh to re-check ports."
            exit 1
        fi
        wait_for_compose_service postgres
        wait_for_compose_service redis
        wait_for_compose_service api
        echo "Full Docker stack rebuilt and started. API available at $(local_api_url)"
        echo "Note: Database migrations are automatically applied on API startup."
        ;;

    reset-db)
        echo "Resetting database (drops all data and reapplies migrations)..."
        cd "$PROJECT_ROOT"
        load_env
        compose_cmd --profile app down -v
        if ! compose_cmd --profile app up -d; then
            echo "Failed to start reset Docker services. Run ./.runtm/setup.local.sh to re-check ports."
            exit 1
        fi
        wait_for_compose_service postgres
        wait_for_compose_service redis
        wait_for_compose_service api
        echo "Database reset complete. All data cleared and migrations applied."
        ;;

    logs)
        cd "$PROJECT_ROOT"
        load_env
        compose_cmd --profile app logs -f "${@:2}"
        ;;

    deps-logs)
        cd "$PROJECT_ROOT"
        load_env
        compose_cmd logs -f postgres redis
        ;;

    logs-docker)
        "$0" logs "${@:2}"
        ;;

    migrate)
        echo "Running database migrations..."
        cd "$PROJECT_ROOT"
        load_env
        activate_venv
        cd "$PROJECT_ROOT/packages/api"
        alembic upgrade head
        ;;

    doctor)
        cd "$PROJECT_ROOT"
        load_env
        activate_venv
        RUNTM_API_URL="$(local_api_url)" \
        RUNTM_API_KEY="${RUNTM_API_KEY:-dev-token-change-in-production}" \
            runtm-dev doctor
        ;;

    doctor-local)
        cd "$PROJECT_ROOT"
        load_env
        # shellcheck disable=SC1091
        source "$PROJECT_ROOT/.runtm/lib/common.sh"
        local_dev_doctor
        ;;

    diagnose-env)
        cd "$PROJECT_ROOT"
        load_env
        # shellcheck disable=SC1091
        source "$PROJECT_ROOT/.runtm/lib/common.sh"

        echo "Local workspace:"
        echo "  Path:           $(workspace_path)"
        echo "  Name:           $(runtm_workspace_name)"
        echo "  Docker project: ${RUNTM_DOCKER_PROJECT:-$(runtm_docker_project)}"
        echo ""

        echo "Selected environment (.env values after script load; secrets masked):"
        print_selected_env
        echo ""

        echo "Generated ports:"
        if [ -f "$PROJECT_ROOT/.runtm/ports.json" ]; then
            cat "$PROJECT_ROOT/.runtm/ports.json"
        else
            echo "  No .runtm/ports.json found. Run ./.runtm/setup.local.sh first."
        fi
        echo ""

        if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
            echo "Docker Compose services:"
            compose_cmd --profile app config --services | sed 's/^/  /'
            echo ""
            echo "Docker Compose containers for this workspace:"
            compose_cmd --profile app ps
            echo ""
            print_local_data_summary
        else
            echo "Docker daemon is not reachable; skipping Compose diagnostics."
        fi
        ;;

    ports)
        cd "$PROJECT_ROOT"
        if [ -f ".runtm/ports.json" ]; then
            cat ".runtm/ports.json"
        else
            echo "No .runtm/ports.json found. Run ./.runtm/setup.local.sh first."
            exit 1
        fi
        ;;

    test)
        echo "Running tests..."
        cd "$PROJECT_ROOT"
        activate_venv
        pytest "${@:2}"
        ;;

    lint)
        echo "Running linter..."
        cd "$PROJECT_ROOT"
        activate_venv
        ruff check .
        ;;

    format)
        echo "Formatting code..."
        cd "$PROJECT_ROOT"
        activate_venv
        ruff format .
        ;;

    check)
        echo "Running all pre-commit checks..."
        cd "$PROJECT_ROOT"
        activate_venv
        ruff check .
        ruff format --check .
        echo "All checks passed!"
        ;;

    *)
        echo "Usage: $0 {setup-local|teardown-local|run-local|setup|up|deps-up|up-docker|down|restart|rebuild-docker|reset-db|logs|deps-logs|logs-docker|migrate|doctor|doctor-local|diagnose-env|ports|test|lint|format|check}"
        echo ""
        echo "Commands:"
        echo "  setup-local    - Create isolated local dev environment for this workspace"
        echo "  teardown-local - Stop this workspace's local services"
        echo "  run-local      - Start API + worker from .venv and follow logs"
        echo "  setup    - Install packages in development mode"
        echo "  up       - Start full Docker stack including API + worker"
        echo "  deps-up  - Start local Postgres + Redis only"
        echo "  up-docker - Alias for up"
        echo "  down     - Stop local services"
        echo "  restart  - Restart full Docker stack"
        echo "  rebuild-docker - Rebuild and restart full Docker stack"
        echo "  reset-db - Reset database (drops all data, reapplies migrations)"
        echo "  logs     - View full Docker stack logs"
        echo "  deps-logs - View Postgres + Redis logs"
        echo "  logs-docker - Alias for logs"
        echo "  migrate  - Run database migrations (manual, for local dev without docker)"
        echo "  doctor   - Check local CLI/API/sandbox setup"
        echo "  doctor-local - Validate generated local ports, Docker project, and .env consistency"
        echo "  diagnose-env - Print local workspace and Compose diagnostics with secrets masked"
        echo "  ports    - Show generated local service ports"
        echo "  test     - Run tests"
        echo "  lint     - Run linter"
        echo "  format   - Format code"
        echo "  check    - Run all checks (lint + format check) without modifying files"
        echo ""
        echo "Note: run-local is the fast host-run path; up preserves the full Docker stack."
        exit 1
        ;;
esac
