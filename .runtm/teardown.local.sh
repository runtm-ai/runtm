#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/common.sh"

REMOVE_VOLUMES=false

for arg in "$@"; do
    case "$arg" in
        --volumes|-v)
            REMOVE_VOLUMES=true
            ;;
        -h|--help)
            cat <<'EOF'
Usage: ./.runtm/teardown.local.sh [--volumes]

Stops the Docker Compose stack for this workspace. Pass --volumes to remove
the workspace database and artifact volumes.
EOF
            exit 0
            ;;
        *)
            fail "Unknown option: $arg"
            ;;
    esac
done

cd "$PROJECT_ROOT"
load_project_env

PROJECT_NAME="${RUNTM_DOCKER_PROJECT:-$(runtm_docker_project)}"

info "Stopping Docker Compose project $PROJECT_NAME"

if [ "$REMOVE_VOLUMES" = true ]; then
    docker compose -p "$PROJECT_NAME" -f infra/docker-compose.yml --env-file .env --profile app down -v
else
    docker compose -p "$PROJECT_NAME" -f infra/docker-compose.yml --env-file .env --profile app down
fi

success "Stopped $PROJECT_NAME"
