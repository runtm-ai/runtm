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
        echo "⚠ No .env file found. Copy infra/local.env.example to .env"
    fi
}

# Fail fast with actionable messages before installing anything
preflight() {
    local failed=0

    # Python >= 3.11 (required by the api and worker packages)
    if ! command -v python3 &> /dev/null; then
        echo "✗ python3 not found in PATH"
        echo "  Install Python 3.11+ from https://www.python.org/downloads/"
        failed=1
    elif ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
        echo "✗ Python 3.11+ required (found $(python3 --version 2>&1))"
        echo "  The api and worker packages require Python >= 3.11."
        echo "  Install a newer Python, then delete .venv and re-run setup."
        failed=1
    else
        echo "✓ $(python3 --version 2>&1)"
    fi

    # Docker (needed for local services, not for package installs)
    if command -v docker &> /dev/null; then
        echo "✓ Docker found"
    else
        echo "⚠ Docker not found - './scripts/dev.sh up' will not work"
        echo "  Install from https://docs.docker.com/get-docker/"
    fi

    if [ "$failed" -ne 0 ]; then
        echo ""
        echo "Setup aborted. Fix the issues above and re-run: ./scripts/dev.sh setup"
        exit 1
    fi
}

case "$1" in
    setup)
        echo "Setting up development environment..."
        cd "$PROJECT_ROOT"

        echo ""
        echo "[1/6] Checking prerequisites..."
        preflight

        # Create .env from the example so services can start out of the box
        if [ ! -f ".env" ]; then
            echo "  Creating .env from infra/local.env.example..."
            cp infra/local.env.example .env
            echo "  ⚠ Edit .env and set FLY_API_TOKEN (run 'fly auth token') to enable deploys"
        fi

        echo ""
        echo "[2/6] Creating virtual environment..."
        if [ ! -d ".venv" ]; then
            python3 -m venv .venv
        else
            echo "  ✓ .venv already exists"
        fi
        source .venv/bin/activate

        echo ""
        echo "[3/6] Upgrading pip..."
        pip install --quiet --upgrade pip

        echo ""
        echo "[4/6] Installing Python packages..."
        for pkg in "packages/shared[dev]" "packages/sandbox[dev]" "packages/agents[dev]" \
                   "packages/api[dev]" "packages/worker[dev]" "packages/cli[dev,sandbox]"; do
            echo "  Installing $pkg..."
            if ! pip install --quiet -e "$pkg"; then
                echo ""
                echo "✗ Failed to install $pkg"
                echo "  Re-run with full output: pip install -e '$pkg'"
                exit 1
            fi
        done

        echo ""
        echo "[5/6] Installing pre-commit hooks..."
        pip install --quiet pre-commit
        pre-commit install

        echo ""
        echo "[6/6] Installing sandbox dependencies..."

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
        echo "Then verify your setup:"
        echo "  ./scripts/dev.sh doctor"
        echo ""
        echo "And start building:"
        echo "  source .venv/bin/activate"
        echo "  runtm-dev start"
        echo "============================================"
        ;;

    doctor)
        cd "$PROJECT_ROOT"
        if [ ! -x ".venv/bin/runtm-dev" ]; then
            echo "✗ runtm-dev is not installed."
            echo "  Run './scripts/dev.sh setup' first."
            exit 1
        fi
        load_env
        .venv/bin/runtm-dev doctor "${@:2}"
        ;;

    up)
        echo "Starting local services..."
        cd "$PROJECT_ROOT"
        load_env
        docker compose -f infra/docker-compose.yml --env-file .env up -d
        echo "Services started. API available at http://localhost:8000"
        ;;

    down)
        echo "Stopping local services..."
        cd "$PROJECT_ROOT"
        docker compose -f infra/docker-compose.yml --env-file .env down
        ;;

    restart)
        echo "Restarting services..."
        cd "$PROJECT_ROOT"
        load_env
        docker compose -f infra/docker-compose.yml --env-file .env down
        docker compose -f infra/docker-compose.yml --env-file .env up -d
        echo "Services restarted. API available at http://localhost:8000"
        ;;

    rebuild)
        echo "Rebuilding and restarting services..."
        cd "$PROJECT_ROOT"
        load_env
        docker compose -f infra/docker-compose.yml --env-file .env build
        docker compose -f infra/docker-compose.yml --env-file .env up -d
        echo "Services rebuilt and started. API available at http://localhost:8000"
        echo "Note: Database migrations are automatically applied on API startup."
        ;;

    reset-db)
        echo "Resetting database (drops all data and reapplies migrations)..."
        cd "$PROJECT_ROOT"
        load_env
        docker compose -f infra/docker-compose.yml --env-file .env down -v
        docker compose -f infra/docker-compose.yml --env-file .env up -d
        echo "Database reset complete. All data cleared and migrations applied."
        ;;

    logs)
        cd "$PROJECT_ROOT"
        docker compose -f infra/docker-compose.yml --env-file .env logs -f "${@:2}"
        ;;

    migrate)
        echo "Running database migrations..."
        cd "$PROJECT_ROOT/packages/api"
        alembic upgrade head
        ;;

    test)
        echo "Running tests..."
        cd "$PROJECT_ROOT"
        pytest "${@:2}"
        ;;

    lint)
        echo "Running linter..."
        cd "$PROJECT_ROOT"
        ruff check .
        ;;

    format)
        echo "Formatting code..."
        cd "$PROJECT_ROOT"
        ruff format .
        ;;

    check)
        echo "Running all pre-commit checks..."
        cd "$PROJECT_ROOT"
        ruff check . && ruff format --check .
        echo "All checks passed!"
        ;;

    *)
        echo "Usage: $0 {setup|doctor|up|down|restart|rebuild|reset-db|logs|migrate|test|lint|format|check}"
        echo ""
        echo "Commands:"
        echo "  setup    - Install packages in development mode"
        echo "  doctor   - Verify the dev environment (Python, .env, Docker, services)"
        echo "  up       - Start local services (auto-loads .env, runs migrations)"
        echo "  down     - Stop local services"
        echo "  restart  - Restart services (auto-loads .env)"
        echo "  rebuild  - Rebuild and restart services (auto-loads .env)"
        echo "  reset-db - Reset database (drops all data, reapplies migrations)"
        echo "  logs     - View service logs (e.g., ./dev.sh logs worker)"
        echo "  migrate  - Run database migrations (manual, for local dev without docker)"
        echo "  test     - Run tests"
        echo "  lint     - Run linter"
        echo "  format   - Format code"
        echo "  check    - Run all checks (lint + format check) without modifying files"
        echo ""
        echo "Note: Migrations are automatically applied when the API container starts."
        exit 1
        ;;
esac
