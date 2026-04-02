# Dockerize the OSS Experience -- TDD Specification

> **Goal:** Make it possible for anyone on any OS to go from `git clone` to a fully working runtm stack in under 5 minutes, using only Docker.

This document specifies every change needed to make the runtm developer and self-hosting experience seamlessly Docker-based and cross-platform. Each item follows a TDD structure: current state, desired state, testable acceptance criteria, and implementation notes.

---

## Table of Contents

- [Tier 1: 10x the Contributor Experience](#tier-1-10x-the-contributor-experience)
  - [1a. Dev Compose with Hot-Reload](#1a-dev-compose-with-hot-reload)
  - [1b. Cross-Platform Dev Tooling](#1b-cross-platform-dev-tooling)
  - [1c. Local Deploy Provider](#1c-local-deploy-provider)
- [Tier 2: Make Self-Hosting Production-Ready](#tier-2-make-self-hosting-production-ready)
  - [2a. HTTPS/TLS via Reverse Proxy](#2a-httpstls-via-reverse-proxy)
  - [2b. Extended Health Endpoint](#2b-extended-health-endpoint)
  - [2c. Production Compose without Local Infra](#2c-production-compose-without-local-infra)
- [Tier 3: Community and Adoption](#tier-3-community-and-adoption)
  - [3a. CONTRIBUTING.md Overhaul](#3a-contributingmd-overhaul)
  - [3b. Multi-Arch Docker Images on GHCR](#3b-multi-arch-docker-images-on-ghcr)
  - [3c. AWS/GCP Provider Stubs](#3c-awsgcp-provider-stubs)

---

## Tier 1: 10x the Contributor Experience

### 1a. Dev Compose with Hot-Reload

#### Current State

`infra/docker-compose.yml` builds images using `infra/Dockerfile.api` and `infra/Dockerfile.worker`, which `COPY` source code into the image at build time. Any code change requires running `./scripts/dev.sh rebuild`, which rebuilds Docker images from scratch (30-60 seconds). There are no volume mounts for application source.

The API entrypoint in `infra/entrypoint-api.sh` runs uvicorn without the `--reload` flag:

```bash
exec sh -c "alembic upgrade head && uvicorn runtm_api.main:app --host 0.0.0.0 --port 8000"
```

The worker in `infra/Dockerfile.worker` runs:

```
CMD ["python", "-m", "runtm_worker.main"]
```

Neither process watches for file changes.

#### Desired State

A new `infra/docker-compose.dev.yml` that:

1. Reuses the same base images but **bind-mounts** source code directories into the containers
2. Runs uvicorn with `--reload` so API changes are picked up instantly
3. Runs the worker with `watchfiles` (or equivalent) for automatic restart on code change
4. Installs packages in editable mode inside the container so imports resolve from mounted volumes

The following source directories need mounting:

| Host Path | Container Path | Service |
|-----------|---------------|---------|
| `packages/shared/runtm_shared` | `/app/packages/shared/runtm_shared` | api, worker |
| `packages/api/runtm_api` | `/app/packages/api/runtm_api` | api |
| `packages/api/alembic` | `/app/packages/api/alembic` | api |
| `packages/worker/runtm_worker` | `/app/packages/worker/runtm_worker` | worker |

Example `api` service override:

```yaml
api:
  build:
    context: ..
    dockerfile: infra/Dockerfile.api
  command: >
    sh -c "alembic upgrade head &&
           uvicorn runtm_api.main:app --host 0.0.0.0 --port 8000 --reload"
  volumes:
    - ../packages/shared/runtm_shared:/app/packages/shared/runtm_shared
    - ../packages/api/runtm_api:/app/packages/api/runtm_api
    - ../packages/api/alembic:/app/packages/api/alembic
    - artifacts:/artifacts
  ports:
    - "8000:8000"
  env_file:
    - ../.env
  environment:
    - DATABASE_URL=postgresql://runtm:runtm@postgres:5432/runtm
    - REDIS_URL=redis://redis:6379
    - DEBUG=true
  depends_on:
    postgres:
      condition: service_healthy
    redis:
      condition: service_started
```

#### Acceptance Criteria

- [ ] `docker compose -f infra/docker-compose.dev.yml up` starts all services (api, worker, postgres, redis)
- [ ] Editing a file in `packages/api/runtm_api/` is reflected in the running API within 3 seconds without manual rebuild
- [ ] Editing a file in `packages/shared/runtm_shared/` is picked up by both API and worker
- [ ] Editing a file in `packages/worker/runtm_worker/` triggers a worker restart
- [ ] `curl http://localhost:8000/health` returns `200 OK` after startup
- [ ] The existing `infra/docker-compose.yml` continues to work unchanged (no breaking changes)

#### Implementation Notes

- The dev Dockerfile may need to install packages with `pip install -e` (editable mode) instead of `pip install` so that Python resolves imports from the mounted volume rather than the installed site-packages copy. Consider a `Dockerfile.dev` that extends the base image with editable installs, or add a `pip install -e` step in the dev compose command.
- For the worker, `watchfiles` is a lightweight Python file watcher. The dev compose command would be: `watchfiles "python -m runtm_worker.main" /app/packages/worker/runtm_worker /app/packages/shared/runtm_shared`. Add `watchfiles` to worker dev dependencies in `packages/worker/pyproject.toml`.
- Volume mounts on Windows may have performance issues with the default storage driver. Document that WSL2 backend for Docker Desktop is recommended on Windows, and that source should live inside the WSL2 filesystem for best performance.

#### Files Affected

- `infra/docker-compose.dev.yml` (new)
- `infra/Dockerfile.dev` (new -- optional, for editable installs)
- `packages/worker/pyproject.toml` (modify -- add `watchfiles` to dev deps)
- `infra/local.env.example` (modify -- document dev compose usage)

---

### 1b. Cross-Platform Dev Tooling

#### Current State

`scripts/dev.sh` is the only dev script. It is a bash script that uses `source`, `set -a`, and other bash-isms. Windows users cannot run it natively -- they must use WSL2. The requirements doc (`docs/self-hosting/requirements.mdx`) lists "Windows (WSL2)" as the OS requirement, making WSL2 a hard dependency.

There is no `Makefile` or PowerShell equivalent anywhere in the repository.

#### Desired State

Three complementary entry points, all delegating to `docker compose`:

1. **`Makefile`** (repo root) -- works on Linux, macOS, and Windows (with `make` installed via chocolatey, scoop, or MSYS2). Primary recommended path.

2. **`scripts/dev.ps1`** -- native PowerShell script for Windows users who don't have `make`. Mirrors the Makefile targets.

3. **`scripts/dev.sh`** -- retained for backward compatibility, unchanged.

Makefile targets:

```makefile
.PHONY: dev up down restart rebuild logs test lint format reset-db

# Development (hot-reload)
dev:
	docker compose -f infra/docker-compose.dev.yml up --build

up:
	docker compose -f infra/docker-compose.yml up -d

down:
	docker compose -f infra/docker-compose.dev.yml down
	docker compose -f infra/docker-compose.yml down 2>/dev/null || true

restart:
	docker compose -f infra/docker-compose.dev.yml down
	docker compose -f infra/docker-compose.dev.yml up --build

rebuild:
	docker compose -f infra/docker-compose.yml build
	docker compose -f infra/docker-compose.yml up -d

logs:
	docker compose -f infra/docker-compose.dev.yml logs -f $(SVC)

test:
	docker compose -f infra/docker-compose.dev.yml exec api pytest /app/packages/api/tests -v
	docker compose -f infra/docker-compose.dev.yml exec api pytest /app/packages/shared/tests -v

lint:
	docker compose -f infra/docker-compose.dev.yml exec api ruff check /app

format:
	docker compose -f infra/docker-compose.dev.yml exec api ruff format /app

reset-db:
	docker compose -f infra/docker-compose.dev.yml down -v
	docker compose -f infra/docker-compose.dev.yml up --build
```

#### Acceptance Criteria

- [ ] `make dev` starts the full stack with hot-reload on Linux and macOS
- [ ] `make dev` works on Windows with `make` installed (via chocolatey/scoop)
- [ ] `scripts/dev.ps1 dev` starts the full stack on native Windows PowerShell (no WSL2 required)
- [ ] `make test` runs the test suite inside Docker containers (no local Python required)
- [ ] `make lint` runs linting inside Docker (no local ruff installation required)
- [ ] `make down` stops all services regardless of which compose file was used
- [ ] `scripts/dev.sh` continues to work unchanged for existing users
- [ ] README.md documents `make dev` as the primary quickstart command

#### Implementation Notes

- The Makefile should use `?=` for variables so users can override (e.g., `make logs SVC=worker`).
- The PowerShell script should use `param()` blocks and mirror each Makefile target as a subcommand: `./scripts/dev.ps1 dev`, `./scripts/dev.ps1 test`, etc.
- All three scripts delegate to `docker compose` -- they are thin wrappers, not independent implementations.
- On Windows, file path separators in volume mounts work correctly with Docker Desktop as long as the Docker backend is WSL2 (which is the default). Document this.

#### Files Affected

- `Makefile` (new)
- `scripts/dev.ps1` (new)
- `README.md` (modify -- update quickstart to show `make dev`)
- `CONTRIBUTING.md` (modify -- add Makefile section)

---

### 1c. Local Deploy Provider

#### Current State

The only `DeployProvider` implementation is `FlyProvider` in `packages/worker/runtm_worker/providers/fly.py`. It requires a valid `FLY_API_TOKEN` environment variable at initialization:

```python
self.api_token = api_token or os.environ.get("FLY_API_TOKEN")
if not self.api_token:
    raise ProviderNotConfiguredError("fly")
```

This means the entire stack fails to start without a Fly.io account. The `infra/local.env.example` marks `FLY_API_TOKEN` as required.

The `DeployProvider` abstract base class in `packages/worker/runtm_worker/providers/base.py` defines 8 abstract methods:

| Method | Purpose |
|--------|---------|
| `name` (property) | Provider identifier |
| `deploy()` | Deploy a container |
| `redeploy()` | Update existing deployment |
| `get_status()` | Check deployment state |
| `destroy()` | Tear down deployment |
| `get_logs()` | Retrieve runtime logs |
| `add_custom_domain()` | Add custom domain |
| `get_custom_domain_status()` | Check domain status |
| `remove_custom_domain()` | Remove custom domain |

Plus one default method: `health_check()`.

#### Desired State

A `LocalProvider(DeployProvider)` that deploys user applications as local Docker containers. This enables the full runtm workflow (init, deploy, logs, destroy) without any external accounts.

```
Provider Selection Flow:

  DEPLOY_PROVIDER env var
         |
    +----+----+
    |         |
  "fly"    "local" (default in dev)
    |         |
FlyProvider  LocalProvider
    |         |
  Fly.io    Local Docker
  Machines  containers
```

How `LocalProvider` would work:

- `deploy()`: Builds a Docker image from the user's artifact, runs it as a container on a local Docker network, assigns a port from a range (e.g., 9000-9999), returns a URL like `http://localhost:9042`
- `redeploy()`: Stops the old container, builds new image, starts new container on the same port
- `get_status()`: Uses Docker API to check container state (`running`, `stopped`, `exited`)
- `destroy()`: Stops and removes the container and image
- `get_logs()`: Uses Docker API to retrieve container logs
- `health_check()`: HTTP GET to the container's health endpoint
- `add_custom_domain()` / `get_custom_domain_status()` / `remove_custom_domain()`: Return stub responses (not applicable for local deploys)

Provider selection would use an env var:

```bash
# in local.env.example
DEPLOY_PROVIDER=local    # "local" or "fly"
```

The worker startup code would use a factory:

```python
def get_provider() -> DeployProvider:
    provider_name = os.environ.get("DEPLOY_PROVIDER", "local")
    if provider_name == "fly":
        return FlyProvider()
    elif provider_name == "local":
        return LocalProvider()
    else:
        raise ValueError(f"Unknown provider: {provider_name}")
```

#### Acceptance Criteria

- [ ] `git clone && cp infra/local.env.example .env && make dev` starts the full stack with zero external accounts
- [ ] `FLY_API_TOKEN` is no longer required when `DEPLOY_PROVIDER=local`
- [ ] `runtm deploy` (against local stack) creates a running Docker container accessible at `http://localhost:<port>`
- [ ] `runtm logs <id>` returns logs from the local container
- [ ] `runtm destroy <id>` removes the local container
- [ ] `runtm status <id>` shows `running` for a healthy local deployment
- [ ] Setting `DEPLOY_PROVIDER=fly` with a valid `FLY_API_TOKEN` continues to work exactly as before
- [ ] `LocalProvider` passes all abstract method contracts (unit tests)
- [ ] Custom domain methods return graceful "not supported" responses without errors

#### Implementation Notes

- `LocalProvider` needs access to the Docker daemon. In `docker-compose.dev.yml`, the worker service would mount the Docker socket: `- /var/run/docker.sock:/var/run/docker.sock`. This is scoped to the worker only.
- On Windows, Docker Desktop exposes the socket at `//var/run/docker.sock` in WSL2 or via named pipe. Both work with the standard mount.
- The `docker` Python package (already in worker dependencies per `packages/worker/pyproject.toml`) provides the Docker API client.
- User artifacts are zip files containing project source + Dockerfile. `LocalProvider.deploy()` would: extract artifact to temp dir, run `docker build`, run `docker run` with the built image.
- A dedicated Docker network (`runtm-deploys`) should isolate user containers from the control plane.
- Port allocation: maintain a simple port map in Redis (or SQLite) keyed by deployment_id. Range: 9000-9999.
- The `local.env.example` should default to `DEPLOY_PROVIDER=local` and mark `FLY_API_TOKEN` as optional (only needed for `DEPLOY_PROVIDER=fly`).

#### Files Affected

- `packages/worker/runtm_worker/providers/local.py` (new)
- `packages/worker/runtm_worker/providers/__init__.py` (modify -- export LocalProvider)
- `packages/worker/runtm_worker/providers/factory.py` (new -- provider factory)
- `packages/worker/tests/test_local_provider.py` (new -- unit tests)
- `infra/local.env.example` (modify -- default to `DEPLOY_PROVIDER=local`, make `FLY_API_TOKEN` optional)
- `infra/docker-compose.dev.yml` (modify -- add Docker socket mount to worker)

---

## Tier 2: Make Self-Hosting Production-Ready

### 2a. HTTPS/TLS via Reverse Proxy

#### Current State

The API listens on raw HTTP port 8000. The TLS middleware in `packages/api/runtm_api/middleware/proxy.py` rejects non-HTTPS requests in production but expects an external reverse proxy to terminate TLS. No reverse proxy is included in the repo.

Self-hosters must configure their own TLS termination (nginx, Caddy, Traefik, etc.) without any guidance or templates.

#### Desired State

A `infra/docker-compose.prod.yml` that includes Caddy as a reverse proxy with automatic HTTPS:

```yaml
caddy:
  image: caddy:2-alpine
  ports:
    - "80:80"
    - "443:443"
  volumes:
    - ../infra/Caddyfile:/etc/caddy/Caddyfile
    - caddy_data:/data
    - caddy_config:/config
  environment:
    - RUNTM_DOMAIN=${RUNTM_DOMAIN:-localhost}
  depends_on:
    - api
```

With a `Caddyfile`:

```
{$RUNTM_DOMAIN} {
    reverse_proxy api:8000
}
```

#### Acceptance Criteria

- [ ] `RUNTM_DOMAIN=runtm.example.com docker compose -f infra/docker-compose.prod.yml up` serves the API over HTTPS with a valid Let's Encrypt certificate
- [ ] `RUNTM_DOMAIN=localhost docker compose -f infra/docker-compose.prod.yml up` serves with Caddy's self-signed local certificate
- [ ] The API's TLS middleware no longer rejects requests (since Caddy terminates TLS and forwards as HTTP)
- [ ] Health check at `https://<domain>/health` returns `200 OK`
- [ ] Certificate data persists across restarts via Docker volume

#### Implementation Notes

- Caddy handles certificate issuance and renewal automatically. No manual certbot or cron needed.
- The `RUNTM_DOMAIN` environment variable must be set. When set to `localhost`, Caddy issues a self-signed certificate suitable for local testing.
- Port 80 is needed for ACME HTTP-01 challenges. Document this requirement.
- The API service in the prod compose should not expose port 8000 directly -- only Caddy should be publicly accessible.

#### Files Affected

- `infra/docker-compose.prod.yml` (new)
- `infra/Caddyfile` (new)
- `infra/local.env.prod.example` (new -- production env template)
- `docs/self-hosting/docker-compose.mdx` (modify -- document prod compose)

---

### 2b. Extended Health Endpoint

#### Current State

`packages/api/runtm_api/routes/health.py` implements a single endpoint:

```python
@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    return HealthResponse(status="healthy", version=__version__)
```

It returns `{status: "healthy", version: "x.y.z"}` unconditionally. There is no check of Postgres, Redis, or worker status. Operators have no visibility into component health without SSHing into containers.

#### Desired State

Add a `GET /health/detailed` endpoint that probes all dependencies:

```python
@router.get("/health/detailed")
async def detailed_health_check(db: AsyncSession, redis: Redis) -> dict:
    result = {
        "status": "healthy",
        "version": __version__,
        "checks": {
            "postgres": "ok",
            "redis": "ok",
            "worker_queue_depth": 0,
        },
    }

    # Check Postgres
    try:
        await db.execute(text("SELECT 1"))
    except Exception as e:
        result["checks"]["postgres"] = f"error: {str(e)}"
        result["status"] = "degraded"

    # Check Redis
    try:
        await redis.ping()
        queue = Queue(connection=redis)
        result["checks"]["worker_queue_depth"] = len(queue)
    except Exception as e:
        result["checks"]["redis"] = f"error: {str(e)}"
        result["status"] = "degraded"

    return result
```

The existing `GET /health` endpoint remains unchanged (fast, no I/O, for load balancer probes).

#### Acceptance Criteria

- [ ] `GET /health` continues to return `{status, version}` with no I/O (sub-millisecond)
- [ ] `GET /health/detailed` returns `{status, version, checks: {postgres, redis, worker_queue_depth}}`
- [ ] When Postgres is unreachable, `checks.postgres` contains an error string and `status` is `"degraded"`
- [ ] When Redis is unreachable, `checks.redis` contains an error string and `status` is `"degraded"`
- [ ] `worker_queue_depth` reflects the actual number of pending jobs in the RQ queue
- [ ] The TLS middleware exempts `/health/detailed` from HTTPS enforcement (like it already does for `/health`)

#### Implementation Notes

- Use FastAPI dependency injection for the database session and Redis connection.
- The detailed endpoint should have a short timeout (5 seconds) to prevent hanging when a dependency is down.
- Consider caching the result for 5-10 seconds to prevent hammering dependencies.
- Add `/health/detailed` to the TLS bypass list in `packages/api/runtm_api/middleware/proxy.py` alongside `/health`, `/healthz`, `/ready`.

#### Files Affected

- `packages/api/runtm_api/routes/health.py` (modify -- add detailed endpoint)
- `packages/api/runtm_api/middleware/proxy.py` (modify -- add `/health/detailed` to bypass)
- `packages/api/tests/test_health.py` (modify -- add tests for detailed endpoint)

---

### 2c. Production Compose without Local Infra

#### Current State

`infra/docker-compose.yml` always includes `postgres` and `redis` services. Self-hosters who use managed Postgres (RDS, Cloud SQL) and managed Redis (ElastiCache, Memorystore) still get local instances started.

The `DATABASE_URL` and `REDIS_URL` env vars in the compose file are hardcoded to point at the local containers:

```yaml
environment:
  - DATABASE_URL=postgresql://runtm:runtm@postgres:5432/runtm
  - REDIS_URL=redis://redis:6379
```

#### Desired State

A `infra/docker-compose.prod.yml` that includes only `api`, `worker`, and `caddy` -- no Postgres or Redis. Connection strings come entirely from environment variables.

A separate `infra/local.env.prod.example` template:

```bash
# Production environment template
# Point these at your managed instances:
DATABASE_URL=postgresql://user:password@your-postgres-host:5432/runtm
REDIS_URL=redis://your-redis-host:6379

# TLS domain (required for HTTPS)
RUNTM_DOMAIN=runtm.example.com

# Fly.io (required for production deploys)
FLY_API_TOKEN=your-fly-token
FLY_ORG=your-org

# API secret
RUNTM_API_SECRET=generate-a-32-char-secret

# Provider
DEPLOY_PROVIDER=fly
```

#### Acceptance Criteria

- [ ] `docker compose -f infra/docker-compose.prod.yml up` starts only api, worker, and caddy
- [ ] No Postgres or Redis containers are started by the prod compose
- [ ] `DATABASE_URL` pointing at an external Postgres works correctly
- [ ] `REDIS_URL` pointing at an external Redis works correctly
- [ ] Missing `DATABASE_URL` or `REDIS_URL` fails fast with a clear error message
- [ ] `infra/local.env.prod.example` documents all required and optional variables

#### Implementation Notes

- The prod compose should require `.env.prod` (or env vars) rather than falling back to local defaults.
- The `api` and `worker` services should not use `depends_on` for postgres/redis (since they don't exist in this file).
- Consider adding a startup healthcheck in the API entrypoint that verifies Postgres and Redis are reachable before starting uvicorn, with a clear error message if not.

#### Files Affected

- `infra/docker-compose.prod.yml` (new -- created in 2a, extended here)
- `infra/local.env.prod.example` (new)
- `docs/self-hosting/docker-compose.mdx` (modify -- add production section)
- `docs/self-hosting/configuration.mdx` (modify -- document managed infra setup)

---

## Tier 3: Community and Adoption

### 3a. CONTRIBUTING.md Overhaul

#### Current State

`CONTRIBUTING.md` documents the bare-metal development setup as the primary path:

1. Fork and clone
2. `cp infra/local.env.example .env` and edit for Fly.io token
3. `./scripts/dev.sh setup` (creates venv, pip installs 6 packages, installs Bun, sandbox-runtime, Claude CLI)
4. Start local services with `./scripts/dev.sh up`

Docker-based development is mentioned only as the `./scripts/dev.sh up` step for infrastructure. The document assumes contributors have Python, pip, and bash available locally.

#### Desired State

Restructure `CONTRIBUTING.md` with Docker-first development as the primary path:

```markdown
## Quick Start (Docker -- recommended)

1. Fork and clone the repo
2. Copy env template: `cp infra/local.env.example .env`
3. Start everything: `make dev`
4. API is running at http://localhost:8000

That's it. No Python, Node.js, or Fly.io account needed.

## Advanced: Bare-Metal Development

If you prefer running without Docker (e.g., for IDE debugging)...
[existing setup instructions moved here]
```

New sections to add:

- **"Running Tests in Docker"**: `make test` runs pytest inside the container
- **"Adding a Provider"**: step-by-step guide using `DeployProvider` interface, referencing `FlyProvider` and `LocalProvider` as examples
- **"Architecture Quick Reference"**: diagram of how packages depend on each other

#### Acceptance Criteria

- [ ] A new contributor can go from clone to running stack with only `make dev` (no local Python/Node/Fly)
- [ ] `CONTRIBUTING.md` lists Docker-first as the primary path
- [ ] Bare-metal development is preserved in an "Advanced" section
- [ ] "Adding a Provider" section exists with interface reference
- [ ] "Running Tests" section shows `make test`

#### Files Affected

- `CONTRIBUTING.md` (modify -- restructure)
- `README.md` (modify -- update quickstart to `make dev`)

---

### 3b. Multi-Arch Docker Images on GHCR

#### Current State

`.github/workflows/ci.yml` includes a `build` job that builds `runtm-api:test` and `runtm-worker:test` images using `docker/build-push-action@v5` with `push: false`. Images are built for verification only and discarded after CI. No images are published anywhere.

Users who want to self-host must clone the repo and build images locally.

#### Desired State

A new GitHub Actions workflow (or an extension of the existing `ci.yml`) that:

1. On push to `main`: builds and pushes `latest` tag
2. On tag push (e.g., `v0.3.0`): builds and pushes version tag
3. Builds for `linux/amd64` and `linux/arm64` (covers x86 servers and Apple Silicon)
4. Pushes to GitHub Container Registry (`ghcr.io/runtm-ai/runtm-api`, `ghcr.io/runtm-ai/runtm-worker`)

Example workflow addition:

```yaml
publish:
  name: Publish Docker Images
  runs-on: ubuntu-latest
  needs: [lint, test-api, test-shared]
  if: github.ref == 'refs/heads/main' || startsWith(github.ref, 'refs/tags/v')
  permissions:
    contents: read
    packages: write
  steps:
    - uses: actions/checkout@v4

    - name: Set up QEMU
      uses: docker/setup-qemu-action@v3

    - name: Set up Docker Buildx
      uses: docker/setup-buildx-action@v3

    - name: Login to GHCR
      uses: docker/login-action@v3
      with:
        registry: ghcr.io
        username: ${{ github.actor }}
        password: ${{ secrets.GITHUB_TOKEN }}

    - name: Docker meta (API)
      id: meta-api
      uses: docker/metadata-action@v5
      with:
        images: ghcr.io/runtm-ai/runtm-api
        tags: |
          type=ref,event=branch
          type=semver,pattern={{version}}
          type=sha

    - name: Build and push API
      uses: docker/build-push-action@v5
      with:
        context: .
        file: infra/Dockerfile.api
        platforms: linux/amd64,linux/arm64
        push: true
        tags: ${{ steps.meta-api.outputs.tags }}

    # Repeat for worker...
```

#### Acceptance Criteria

- [ ] `docker pull ghcr.io/runtm-ai/runtm-api:latest` works on x86_64 Linux
- [ ] `docker pull ghcr.io/runtm-ai/runtm-api:latest` works on ARM64 (Apple Silicon Mac, Raspberry Pi)
- [ ] `docker pull ghcr.io/runtm-ai/runtm-worker:latest` works on both architectures
- [ ] Pushing a tag `v0.3.0` produces images tagged `0.3.0` and `latest`
- [ ] Pushing to `main` produces images tagged `main` and `latest`
- [ ] Self-hosters can use pre-built images in their compose files instead of building from source
- [ ] `infra/docker-compose.yml` is updated (or a variant provided) that uses `ghcr.io/runtm-ai/runtm-api:latest` instead of `build:` directives

#### Implementation Notes

- QEMU is needed for cross-platform builds on GitHub's x86 runners.
- The Dockerfiles may need minor adjustments for ARM compatibility (mainly ensuring no x86-only binaries are installed). `flyctl` installation in the Dockerfiles currently uses `curl -L https://fly.io/install.sh | sh` -- verify this works on ARM.
- Consider using Docker layer caching (`cache-from`, `cache-to`) to speed up CI builds.
- The existing `build` job in `ci.yml` can be extended or replaced by this workflow.

#### Files Affected

- `.github/workflows/ci.yml` (modify -- add publish job)
- `infra/docker-compose.yml` (modify -- add commented-out image-based alternative)

---

### 3c. AWS/GCP Provider Stubs

> **Status: Future scope.** Documented here for roadmap visibility but not specified in detail.

The `DeployProvider` interface in `packages/worker/runtm_worker/providers/base.py` already notes `CloudRunProvider` as a future implementation. The docstring reads:

```python
class DeployProvider(ABC):
    """Abstract interface for deployment providers.

    Implementations:
        - FlyProvider: Fly.io Machines
        - CloudRunProvider: Google Cloud Run (future)
    """
```

Future providers would implement the same 8 abstract methods:

| Provider | Deploy Target | Auth | Status |
|----------|--------------|------|--------|
| `FlyProvider` | Fly.io Machines | `FLY_API_TOKEN` | Implemented |
| `LocalProvider` | Local Docker | Docker socket | Specified (1c) |
| `AWSProvider` | AWS Fargate / ECS | `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` | Planned |
| `GCPProvider` | Google Cloud Run | `GOOGLE_APPLICATION_CREDENTIALS` | Planned |

These are out of scope for this specification but should be tracked as separate feature requests.

---

## Summary: Implementation Priority

| Priority | Item | Effort | Impact |
|----------|------|--------|--------|
| P0 | 1a. Dev compose with hot-reload | Small | High -- eliminates rebuild cycle |
| P0 | 1b. Cross-platform dev tooling (Makefile) | Small | High -- enables Windows users |
| P1 | 1c. Local deploy provider | Medium | High -- eliminates Fly.io requirement |
| P2 | 2a. HTTPS/TLS via Caddy | Small | Medium -- production self-hosting |
| P2 | 2b. Extended health endpoint | Small | Medium -- operational visibility |
| P2 | 2c. Production compose | Small | Medium -- managed infra support |
| P3 | 3a. CONTRIBUTING.md overhaul | Small | Medium -- contributor onboarding |
| P3 | 3b. Multi-arch images on GHCR | Medium | Medium -- adoption without building |
| P4 | 3c. AWS/GCP providers | Large | High -- but future scope |

**Recommended implementation order:** 1a -> 1b -> 1c -> 2a/2b/2c -> 3a -> 3b
