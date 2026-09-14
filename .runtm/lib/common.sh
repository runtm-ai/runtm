#!/usr/bin/env bash

set -euo pipefail

RUNTM_DEV_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$(cd "$RUNTM_DEV_DIR/.." && pwd)"
RUNTM_STATE_HOME="${RUNTM_STATE_HOME:-$HOME/.runtm}"
PORT_ALLOCATIONS_FILE="$RUNTM_STATE_HOME/port-allocations.tsv"
PORT_LOCK_DIR="$RUNTM_STATE_HOME/port-allocations.lock"
PORT_WINDOW_SIZE=20
PORT_START=18000
PORT_END=24000

info() {
    printf '==> %s\n' "$*"
}

success() {
    printf 'OK  %s\n' "$*"
}

warn() {
    printf 'WARN %s\n' "$*" >&2
}

fail() {
    printf 'ERR %s\n' "$*" >&2
    exit 1
}

workspace_path() {
    cd "$PROJECT_ROOT" && pwd -P
}

hash_string() {
    local value="$1"

    if command -v shasum >/dev/null 2>&1; then
        printf '%s' "$value" | shasum -a 256 | awk '{print substr($1, 1, 8)}'
    elif command -v sha256sum >/dev/null 2>&1; then
        printf '%s' "$value" | sha256sum | awk '{print substr($1, 1, 8)}'
    else
        printf '%s' "$value" | cksum | awk '{print $1}'
    fi
}

sanitize_name() {
    local value="$1"
    value="$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')"
    value="$(printf '%s' "$value" | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//; s/-+/-/g')"

    if [ -z "$value" ]; then
        value="workspace"
    fi

    printf '%s' "$value"
}

is_integer() {
    local value="$1"

    case "$value" in
        ''|*[!0-9]*) return 1 ;;
        *) return 0 ;;
    esac
}

is_valid_port() {
    local port="$1"

    is_integer "$port" && [ "$port" -ge 1 ] && [ "$port" -le 65535 ]
}

is_valid_port_window_base() {
    local base="$1"

    is_valid_port "$base" \
        && [ "$base" -ge "$PORT_START" ] \
        && [ "$((base + 2))" -le "$PORT_END" ]
}

runtm_workspace_name() {
    local path base hash
    path="$(workspace_path)"
    base="$(sanitize_name "$(basename "$path")")"
    hash="$(hash_string "$path")"
    printf '%s-%s' "$base" "$hash"
}

runtm_docker_project() {
    local name
    name="runtm-$(runtm_workspace_name)"
    printf '%s' "$name" | cut -c 1-63
}

write_env_var() {
    local key="$1"
    local value="$2"
    local env_file="$PROJECT_ROOT/.env"
    local tmp_file

    tmp_file="$(mktemp)"
    touch "$env_file"
    grep -v -E "^${key}=" "$env_file" > "$tmp_file" || true
    printf '%s=%s\n' "$key" "$value" >> "$tmp_file"
    mv "$tmp_file" "$env_file"
}

load_project_env() {
    if [ -f "$PROJECT_ROOT/.env" ]; then
        set -a
        # shellcheck disable=SC1091
        source "$PROJECT_ROOT/.env"
        set +a
    fi
}

acquire_port_lock() {
    mkdir -p "$RUNTM_STATE_HOME"

    local attempt
    for attempt in $(seq 1 60); do
        if mkdir "$PORT_LOCK_DIR" 2>/dev/null; then
            printf '%s\n' "$$" > "$PORT_LOCK_DIR/pid"
            return 0
        fi

        if [ -f "$PORT_LOCK_DIR/pid" ]; then
            local pid
            pid="$(cat "$PORT_LOCK_DIR/pid" 2>/dev/null || true)"
            if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
                rm -rf "$PORT_LOCK_DIR"
                continue
            fi
        fi

        sleep 0.25
    done

    fail "Could not acquire port allocation lock at $PORT_LOCK_DIR"
}

release_port_lock() {
    rm -rf "$PORT_LOCK_DIR"
}

port_is_available() {
    local port="$1"

    if command -v python3 >/dev/null 2>&1; then
        python3 - "$port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    sock.bind(("127.0.0.1", port))
except OSError:
    raise SystemExit(1)
finally:
    sock.close()
PY
        return $?
    fi

    if command -v lsof >/dev/null 2>&1; then
        ! lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
        return $?
    fi

    return 0
}

port_serves_runtm_api() {
    local port="$1"

    command -v curl >/dev/null 2>&1 || return 1
    curl -fsS --max-time 1 "http://127.0.0.1:$port/health" 2>/dev/null \
        | grep -q '"status":"healthy"'
}

port_is_workspace_run_local_api() {
    local port="$1"
    local pid_file="$PROJECT_ROOT/.runtm/run.local.pids"
    local api_pid=""

    [ -f "$pid_file" ] || return 1
    api_pid="$(awk -F '=' '$1 == "api" { print $2 }' "$pid_file" | tail -n 1)"
    [ -n "$api_pid" ] || return 1
    kill -0 "$api_pid" 2>/dev/null || return 1
    port_serves_runtm_api "$port"
}

port_is_workspace_compose_port() {
    local port="$1"
    local project

    project="$(runtm_docker_project)"

    command -v docker >/dev/null 2>&1 || return 1
    docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null \
        | awk -v project="$project" -v port="$port" '
            $1 ~ "^" project "-" && index($0, ":" port "->") { found = 1 }
            END { exit(found ? 0 : 1) }
        '
}

workspace_compose_container_name() {
    local service="$1"
    local project

    project="$(runtm_docker_project)"

    command -v docker >/dev/null 2>&1 || return 1
    docker ps --format '{{.Names}}' 2>/dev/null \
        | awk -v project="$project" -v service="$service" '
            $0 ~ "^" project "-" service "-[0-9]+$" { print; found = 1; exit }
            END { exit(found ? 0 : 1) }
        '
}

describe_port_owner() {
    local port="$1"
    local owner=""

    if port_is_workspace_compose_port "$port"; then
        owner="$(docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null \
            | awk -v project="$(runtm_docker_project)" -v port="$port" '
                $1 ~ "^" project "-" && index($0, ":" port "->") { print $1; exit }
            ')"
        printf 'current workspace Docker service (%s)' "${owner:-unknown}"
        return 0
    fi

    if port_is_workspace_run_local_api "$port"; then
        printf 'current workspace run-local API process'
        return 0
    fi

    if port_serves_runtm_api "$port"; then
        printf 'Runtm API process from another or unknown workspace'
        return 0
    fi

    if command -v lsof >/dev/null 2>&1; then
        owner="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null \
            | awk 'NR == 2 { printf "%s pid=%s", $1, $2 }')"
    fi

    if [ -n "$owner" ]; then
        printf '%s' "$owner"
    else
        printf 'unknown process'
    fi
}

port_is_available_for_existing_workspace() {
    local port="$1"
    local role="$2"

    if port_is_available "$port"; then
        return 0
    fi

    if port_is_workspace_compose_port "$port"; then
        return 0
    fi

    if [ "$role" = "api" ] && port_is_workspace_run_local_api "$port"; then
        return 0
    fi

    return 1
}

port_window_is_available() {
    local base="$1"
    local port

    for port in "$base" "$((base + 1))" "$((base + 2))"; do
        if ! port_is_available "$port"; then
            return 1
        fi
    done

    return 0
}

existing_port_window_is_usable() {
    local base="$1"

    port_is_available_for_existing_workspace "$base" "api" \
        && port_is_available_for_existing_workspace "$((base + 1))" "postgres" \
        && port_is_available_for_existing_workspace "$((base + 2))" "redis"
}

describe_port_window_conflicts() {
    local base="$1"
    local port role

    for role in api postgres redis; do
        case "$role" in
            api) port="$base" ;;
            postgres) port="$((base + 1))" ;;
            redis) port="$((base + 2))" ;;
        esac

        if ! port_is_available_for_existing_workspace "$port" "$role"; then
            printf '  - %s port %s is used by %s\n' "$role" "$port" "$(describe_port_owner "$port")"
        fi
    done
}

cleanup_port_allocations() {
    local tmp_file workspace base extra

    mkdir -p "$RUNTM_STATE_HOME"
    touch "$PORT_ALLOCATIONS_FILE"
    tmp_file="$(mktemp)"

    while IFS=$'\t' read -r workspace base extra; do
        [ -n "${workspace:-}" ] || continue
        [ -z "${extra:-}" ] || continue
        [ -d "$workspace" ] || continue
        is_valid_port_window_base "$base" || continue
        printf '%s\t%s\n' "$workspace" "$base" >> "$tmp_file"
    done < "$PORT_ALLOCATIONS_FILE"

    mv "$tmp_file" "$PORT_ALLOCATIONS_FILE"
}

record_port_allocation() {
    local workspace="$1"
    local base="$2"
    local tmp_file

    tmp_file="$(mktemp)"
    touch "$PORT_ALLOCATIONS_FILE"
    awk -F '\t' -v workspace="$workspace" '$1 != workspace { print }' "$PORT_ALLOCATIONS_FILE" > "$tmp_file" || true
    printf '%s\t%s\n' "$workspace" "$base" >> "$tmp_file"
    mv "$tmp_file" "$PORT_ALLOCATIONS_FILE"
}

allocate_port_base() {
    local workspace used_bases existing base last_base
    workspace="$(workspace_path)"

    mkdir -p "$RUNTM_STATE_HOME"
    touch "$PORT_ALLOCATIONS_FILE"

    acquire_port_lock
    trap release_port_lock RETURN

    cleanup_port_allocations

    existing="$(awk -F '\t' -v workspace="$workspace" '$1 == workspace { print $2 }' "$PORT_ALLOCATIONS_FILE" | tail -n 1)"
    if [ -n "$existing" ]; then
        if existing_port_window_is_usable "$existing"; then
            success "Reusing workspace port window $existing (api=$existing, postgres=$((existing + 1)), redis=$((existing + 2)))" >&2
            printf '%s\n' "$existing"
            return 0
        fi

        warn "Existing workspace port window $existing has conflicts:" >&2
        describe_port_window_conflicts "$existing" >&2
        warn "Allocating a new port window and updating .env/.runtm/ports.json." >&2
    fi

    used_bases="$(awk -F '\t' -v workspace="$workspace" '$1 != workspace { print $2 }' "$PORT_ALLOCATIONS_FILE" | tr '\n' ' ')"
    last_base="$((PORT_END - 2))"

    for base in $(seq "$PORT_START" "$PORT_WINDOW_SIZE" "$last_base"); do
        case " $used_bases " in
            *" $base "*) continue ;;
        esac

        if port_window_is_available "$base"; then
            record_port_allocation "$workspace" "$base"
            success "Allocated workspace port window $base (api=$base, postgres=$((base + 1)), redis=$((base + 2)))" >&2
            printf '%s\n' "$base"
            return 0
        fi
    done

    fail "No free Runtm local port window found between $PORT_START and $PORT_END"
}

write_ports_json() {
    local port_base="$1"
    local api_port="$2"
    local postgres_port="$3"
    local redis_port="$4"
    local api_url="$5"
    local workspace docker_project

    workspace="$(runtm_workspace_name)"
    docker_project="$(runtm_docker_project)"
    mkdir -p "$PROJECT_ROOT/.runtm"

    cat > "$PROJECT_ROOT/.runtm/ports.json" <<EOF
{
  "workspace": "$workspace",
  "dockerProject": "$docker_project",
  "portBase": $port_base,
  "services": {
    "api": {
      "port": $api_port,
      "url": "$api_url"
    },
    "postgres": {
      "port": $postgres_port,
      "url": "postgresql://runtm:runtm@127.0.0.1:$postgres_port/runtm"
    },
    "redis": {
      "port": $redis_port,
      "url": "redis://127.0.0.1:$redis_port"
    }
  },
  "environment": {
    "RUNTM_API_URL": "$api_url",
    "RUNTM_API_KEY_SOURCE": ".env:RUNTM_API_KEY"
  },
  "commands": {
    "setup": "./.runtm/setup.local.sh",
    "run": "./scripts/dev.sh run-local",
    "dependencies": "./scripts/dev.sh deps-up",
    "fullDocker": "./scripts/dev.sh up",
    "doctor": "./scripts/dev.sh doctor-local",
    "diagnose": "./scripts/dev.sh diagnose-env",
    "teardown": "./.runtm/teardown.local.sh"
  }
}
EOF
}

write_config_overlay() {
    cat > "$PROJECT_ROOT/.runtm/config.local.json" <<'EOF'
{
  "setup": ["./.runtm/setup.local.sh"],
  "teardown": ["./.runtm/teardown.local.sh"],
  "run": ["./.runtm/run.local.sh"]
}
EOF
}

json_value() {
    local file="$1"
    local path="$2"

    python3 - "$file" "$path" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as f:
    value = json.load(f)

for part in sys.argv[2].split("."):
    value = value[part]

if isinstance(value, bool):
    print("true" if value else "false")
else:
    print(value)
PY
}

env_file_value() {
    local key="$1"
    local env_file="${2:-$PROJECT_ROOT/.env}"

    [ -f "$env_file" ] || return 1
    awk -v key="$key" '
        index($0, key "=") == 1 {
            print substr($0, length(key) + 2)
            found = 1
        }
        END { exit(found ? 0 : 1) }
    ' "$env_file"
}

doctor_ok() {
    success "$*"
}

doctor_warn() {
    warn "$*"
    RUNTM_DOCTOR_WARNINGS=$((RUNTM_DOCTOR_WARNINGS + 1))
}

doctor_error() {
    printf 'ERR %s\n' "$*" >&2
    RUNTM_DOCTOR_ERRORS=$((RUNTM_DOCTOR_ERRORS + 1))
}

doctor_compare() {
    local label="$1"
    local actual="$2"
    local expected="$3"

    if [ "$actual" = "$expected" ]; then
        doctor_ok "$label matches $expected"
    else
        doctor_error "$label is $actual, expected $expected"
    fi
}

doctor_check_port_state() {
    local role="$1"
    local port="$2"

    if ! is_valid_port "$port"; then
        doctor_error "$role port is not valid: $port"
        return 0
    fi

    if port_is_workspace_compose_port "$port"; then
        doctor_ok "$role port $port is owned by this workspace's Docker Compose project"
        return 0
    fi

    if [ "$role" = "api" ] && port_is_workspace_run_local_api "$port"; then
        doctor_ok "api port $port is owned by this workspace's run-local process"
        return 0
    fi

    if port_is_available "$port"; then
        if [ "$role" = "api" ]; then
            doctor_ok "api port $port is free for run-local"
        else
            doctor_warn "$role port $port is free; run ./scripts/dev.sh deps-up to start dependencies"
        fi
        return 0
    fi

    doctor_error "$role port $port is occupied by $(describe_port_owner "$port")"
}

doctor_check_run_local_pids() {
    local pid_file="$PROJECT_ROOT/.runtm/run.local.pids"
    local role pid stale=false

    [ -f "$pid_file" ] || return 0

    while IFS='=' read -r role pid; do
        [ -n "${role:-}" ] || continue
        [ -n "${pid:-}" ] || continue
        if kill -0 "$pid" 2>/dev/null; then
            doctor_ok "run-local $role pid $pid is alive"
        else
            doctor_warn "run-local pid file contains stale $role pid $pid"
            stale=true
        fi
    done < "$pid_file"

    if [ "$stale" = true ]; then
        doctor_warn "remove $pid_file or rerun ./scripts/dev.sh run-local to refresh it"
    fi
}

doctor_check_postgres() {
    local container schema_version table_count smoke_result

    container="$(workspace_compose_container_name postgres || true)"
    if [ -z "$container" ]; then
        doctor_warn "Postgres container is not running; run ./scripts/dev.sh deps-up"
        return 0
    fi

    if docker exec "$container" pg_isready -U runtm -d runtm >/dev/null 2>&1; then
        doctor_ok "Postgres accepts connections in $container"
    else
        doctor_error "Postgres is not ready in $container"
        return 0
    fi

    schema_version="$(docker exec "$container" psql -U runtm -d runtm -Atc 'select version_num from alembic_version limit 1;' 2>/dev/null || true)"
    if [ -n "$schema_version" ]; then
        doctor_ok "Postgres schema version is $schema_version"
    else
        doctor_error "Could not read Postgres alembic_version"
    fi

    smoke_result="$(docker exec "$container" psql -U runtm -d runtm -Atc "create temp table runtm_doctor_smoke(value text); insert into runtm_doctor_smoke values ('ok'); select value from runtm_doctor_smoke;" 2>/dev/null || true)"
    if printf '%s\n' "$smoke_result" | grep -q '^ok$'; then
        doctor_ok "Postgres read/write smoke passed"
    else
        doctor_error "Postgres read/write smoke failed"
    fi

    table_count="$(docker exec "$container" psql -U runtm -d runtm -Atc "select count(*) from information_schema.tables where table_schema = 'public';" 2>/dev/null || true)"
    if [ -n "$table_count" ]; then
        doctor_ok "Postgres public table count is $table_count"
    else
        doctor_warn "Could not read Postgres table count"
    fi
}

doctor_check_redis() {
    local container keyspace smoke_key smoke_value

    container="$(workspace_compose_container_name redis || true)"
    if [ -z "$container" ]; then
        doctor_warn "Redis container is not running; run ./scripts/dev.sh deps-up"
        return 0
    fi

    if docker exec "$container" redis-cli ping 2>/dev/null | grep -q '^PONG$'; then
        doctor_ok "Redis responds to PING in $container"
    else
        doctor_error "Redis did not respond to PING in $container"
        return 0
    fi

    smoke_key="runtm:doctor:$$"
    docker exec "$container" redis-cli set "$smoke_key" ok EX 5 >/dev/null 2>&1 || true
    smoke_value="$(docker exec "$container" redis-cli get "$smoke_key" 2>/dev/null | tr -d '\r' || true)"
    docker exec "$container" redis-cli del "$smoke_key" >/dev/null 2>&1 || true
    if [ "$smoke_value" = "ok" ]; then
        doctor_ok "Redis read/write smoke passed"
    else
        doctor_error "Redis read/write smoke failed"
    fi

    keyspace="$(docker exec "$container" redis-cli info keyspace 2>/dev/null | awk '/^db[0-9]+:/ { print }' | tr -d '\r' | paste -sd ',' - | sed 's/,/, /g')"
    if [ -n "$keyspace" ]; then
        doctor_ok "Redis keyspace: $keyspace"
    else
        doctor_ok "Redis keyspace is empty"
    fi
}

print_local_data_summary() {
    local postgres_container redis_container table_counts redis_keys

    postgres_container="$(workspace_compose_container_name postgres || true)"
    redis_container="$(workspace_compose_container_name redis || true)"

    if [ -n "$postgres_container" ]; then
        printf 'Postgres data summary (%s):\n' "$postgres_container"
        docker exec "$postgres_container" psql -U runtm -d runtm -Atc "
select 'schema_version', version_num from alembic_version
union all select 'api_keys', count(*)::text from api_keys
union all select 'deployments', count(*)::text from deployments
union all select 'telemetry_events', count(*)::text from telemetry_events
union all select 'usage_events', count(*)::text from usage_events
union all select 'usage_counters', count(*)::text from usage_counters
order by 1;" 2>/dev/null \
            | awk -F '|' '{ printf "  %-18s %s\n", $1, $2 }' || true
        table_counts="$(docker exec "$postgres_container" psql -U runtm -d runtm -Atc "select count(*) from information_schema.tables where table_schema = 'public';" 2>/dev/null || true)"
        [ -n "$table_counts" ] && printf '  %-18s %s\n' "public_tables" "$table_counts"
    else
        printf 'Postgres data summary: container is not running\n'
    fi

    if [ -n "$redis_container" ]; then
        printf 'Redis data summary (%s):\n' "$redis_container"
        redis_keys="$(docker exec "$redis_container" redis-cli --scan 2>/dev/null | paste -sd ',' - | sed 's/,/, /g')"
        if [ -n "$redis_keys" ]; then
            printf '  keys              %s\n' "$redis_keys"
        else
            printf '  keys              <empty>\n'
        fi
        docker exec "$redis_container" redis-cli info keyspace 2>/dev/null \
            | awk '/^db[0-9]+:/ { gsub(/\r/, ""); printf "  keyspace          %s\n", $0 }' || true
    else
        printf 'Redis data summary: container is not running\n'
    fi
}

local_dev_doctor() {
    local ports_file="$PROJECT_ROOT/.runtm/ports.json"
    local env_file="$PROJECT_ROOT/.env"
    local workspace docker_project port_base api_port postgres_port redis_port
    local expected_workspace expected_project expected_api_url expected_db_url expected_redis_url
    local env_workspace env_project env_port_base env_api_port env_postgres_port env_redis_port
    local env_api_url env_db_url env_redis_url api_bind containers

    RUNTM_DOCTOR_ERRORS=0
    RUNTM_DOCTOR_WARNINGS=0

    if [ -f "$env_file" ]; then
        doctor_ok "Found .env"
    else
        doctor_error "Missing .env; run ./.runtm/setup.local.sh"
    fi

    if [ -f "$ports_file" ]; then
        doctor_ok "Found .runtm/ports.json"
    else
        doctor_error "Missing .runtm/ports.json; run ./.runtm/setup.local.sh"
    fi

    if ! command -v python3 >/dev/null 2>&1; then
        doctor_error "python3 is required to read .runtm/ports.json"
    fi

    if [ "$RUNTM_DOCTOR_ERRORS" -gt 0 ]; then
        fail "Local development diagnostics failed with $RUNTM_DOCTOR_ERRORS error(s)"
    fi

    workspace="$(json_value "$ports_file" "workspace")"
    docker_project="$(json_value "$ports_file" "dockerProject")"
    port_base="$(json_value "$ports_file" "portBase")"
    api_port="$(json_value "$ports_file" "services.api.port")"
    postgres_port="$(json_value "$ports_file" "services.postgres.port")"
    redis_port="$(json_value "$ports_file" "services.redis.port")"
    expected_api_url="$(json_value "$ports_file" "services.api.url")"
    expected_db_url="$(json_value "$ports_file" "services.postgres.url")"
    expected_redis_url="$(json_value "$ports_file" "services.redis.url")"
    expected_workspace="$(runtm_workspace_name)"
    expected_project="$(runtm_docker_project)"

    doctor_compare "workspace" "$workspace" "$expected_workspace"
    doctor_compare "Docker project" "$docker_project" "$expected_project"

    env_workspace="$(env_file_value RUNTM_WORKSPACE_NAME "$env_file" || true)"
    env_project="$(env_file_value RUNTM_DOCKER_PROJECT "$env_file" || true)"
    env_port_base="$(env_file_value RUNTM_PORT_BASE "$env_file" || true)"
    env_api_port="$(env_file_value RUNTM_API_PORT "$env_file" || true)"
    env_postgres_port="$(env_file_value RUNTM_POSTGRES_PORT "$env_file" || true)"
    env_redis_port="$(env_file_value RUNTM_REDIS_PORT "$env_file" || true)"
    env_api_url="$(env_file_value RUNTM_API_URL "$env_file" || true)"
    env_db_url="$(env_file_value DATABASE_URL "$env_file" || true)"
    env_redis_url="$(env_file_value REDIS_URL "$env_file" || true)"
    api_bind="$(env_file_value RUNTM_API_BIND "$env_file" || true)"

    doctor_compare ".env RUNTM_WORKSPACE_NAME" "$env_workspace" "$expected_workspace"
    doctor_compare ".env RUNTM_DOCKER_PROJECT" "$env_project" "$expected_project"
    doctor_compare ".env RUNTM_PORT_BASE" "$env_port_base" "$port_base"
    doctor_compare ".env RUNTM_API_PORT" "$env_api_port" "$api_port"
    doctor_compare ".env RUNTM_POSTGRES_PORT" "$env_postgres_port" "$postgres_port"
    doctor_compare ".env RUNTM_REDIS_PORT" "$env_redis_port" "$redis_port"
    doctor_compare ".env RUNTM_API_URL" "$env_api_url" "$expected_api_url"
    doctor_compare ".env DATABASE_URL" "$env_db_url" "$expected_db_url"
    doctor_compare ".env REDIS_URL" "$env_redis_url" "$expected_redis_url"

    if [ "$api_bind" = "127.0.0.1" ] || [ "$api_bind" = "localhost" ]; then
        doctor_ok ".env RUNTM_API_BIND is localhost-only ($api_bind)"
    elif [ -n "$api_bind" ]; then
        doctor_warn ".env RUNTM_API_BIND is $api_bind; this can expose the Docker API port beyond this machine"
    else
        doctor_warn ".env RUNTM_API_BIND is missing; Docker Compose will use its default bind"
    fi

    if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
        doctor_ok "Docker daemon is reachable"
        containers="$(docker ps --format '{{.Names}}' 2>/dev/null | awk -v project="$expected_project" '$0 ~ "^" project "-" { print }' | tr '\n' ' ')"
        if [ -n "$containers" ]; then
            doctor_ok "Current workspace Docker containers: $containers"
        else
            doctor_warn "No Docker containers are running for project $expected_project"
        fi
    else
        doctor_error "Docker daemon is not reachable; local Postgres/Redis need Docker"
    fi

    doctor_check_port_state "api" "$api_port"
    doctor_check_port_state "postgres" "$postgres_port"
    doctor_check_port_state "redis" "$redis_port"
    doctor_check_postgres
    doctor_check_redis
    doctor_check_run_local_pids

    if [ "$RUNTM_DOCTOR_ERRORS" -gt 0 ]; then
        fail "Local development diagnostics failed with $RUNTM_DOCTOR_ERRORS error(s) and $RUNTM_DOCTOR_WARNINGS warning(s)"
    fi

    success "Local development diagnostics passed with $RUNTM_DOCTOR_WARNINGS warning(s)"
}
