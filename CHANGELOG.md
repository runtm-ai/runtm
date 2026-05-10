# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.21] - 2026-05-10

### Fixed

- **API**: Fixed tier override in `create_deployment` silently dropping `volumes`, `env_schema`, `connections`, `policy`, and `features` from the manifest
  - When `--tier` query param was provided, the route reconstructed a new `Manifest` from scratch with only basic fields (`name`, `template`, `runtime`, `health_path`, `port`, `tier`), discarding all other fields
  - This caused persistent volumes configured in the UI (`deploy_volumes`) to never reach the worker, so Fly volumes and `[[mounts]]` were never created
  - Fix uses `model_dump()` + `model_validate()` to override only the tier while preserving the full manifest
  - Affects all session deploys (which always pass `--tier`), `env_schema`, `features.database`, and volume mounts

## [0.2.20] - 2026-05-09

### Fixed

- **Worker**: Increased `flyctl deploy --wait-timeout` from 3m to 5m to prevent spurious health check timeouts
  - The 120s `grace_period` plus app boot time left only ~40s for health checks to pass within the old 3m window
  - Deployments that were actually healthy would fail with `timeout reached waiting for health checks to pass` followed by `net/http: request canceled`
  - 5m wait gives ~3 minutes after grace period for the app to start and pass checks
- **Shared**: Fixed stale comments on `Limits.BUILD_TIMEOUT_SECONDS` (15 min, not 10) and `Limits.DEPLOY_TIMEOUT_SECONDS` (10 min, not 5)

## [0.2.19] - 2026-05-09

### Added

- **Worker**: Persistent volume support in the remote builder deploy path
  - `DockerBuilder.build_remote()` now generates `[[mounts]]` sections in `fly.toml` when manifest declares volumes
  - Fly volumes are pre-created via the Machines API before `flyctl deploy` (idempotent — skips existing volumes in the same region)
  - `DeployJob` parses `manifest.volumes` and passes `VolumeConfig` list through to both remote and local builder paths
  - Enables end-to-end persistent storage: UI defines volumes → `runtm.yaml` → worker creates Fly volumes → `flyctl deploy` attaches them

## [0.2.18] - 2026-05-03

### Fixed

- **API**: Aligned DB connection pool config with cloud backend to fix recurring SSL connection drops
  - Increased `pool_size` from 5 to 10 to match cloud backend capacity
  - Added `pool_use_lifo=True` to prefer freshest connections, preventing PgBouncer stale SSL sessions
  - Resolves `psycopg2.OperationalError: SSL connection has been closed unexpectedly` errors seen in production (Datadog issue `c8209896`)

## [0.2.17] - 2026-04-02

### Fixed

- **Worker**: Removed Depot builder and always use BuildKit for remote builds
  - Depot requires mTLS / `DEPOT_TOKEN` auth that is unavailable inside Fly worker machines, causing a consistent 5-minute timeout before falling back to BuildKit on every deploy
  - BuildKit communicates through Fly's internal registry mirror and only needs `FLY_API_TOKEN`, which is already present on the worker machine

### Changed

- **Worker**: `LogCapture.write()` now prints each log line to stdout in real time
  - Output format: `[build] [2026-04-02 19:51:15] <message>` / `[deploy] [2026-04-02 19:58:43] <message>`
  - Enables live visibility of build and deploy progress via `fly logs`
  - Uses `flush=True` to prevent buffering in containerised environments

## [0.2.16] - 2026-04-02

### Changed

- **Worker**: Upgraded RQ to latest version and improved worker reliability
  - Redis connection now uses keepalive and retry-on-timeout to handle flaky Fly Upstash 6PN connections
  - Worker startup warms up the Redis connection before registering birth to prevent `MULTI/EXEC` transaction failures
  - Auto-restart loop (up to 5 restarts) added to `start-worker.sh` for resilience against Redis connection drops
- **Worker**: Added minimal HTTP health-check server on `:9111` so Fly bluegreen deploys correctly start worker machines

## [0.2.15] - 2026-02-23

### Fixed

- **Lint**: Moved `pathspec` import to module level in `api_client.py`; removed quoted return type annotation from `build_ignore_spec` (fixes `UP037` + `F821`)

## [0.2.14] - 2026-02-23

### Fixed

- **Validate**: Python syntax validation now skips irrelevant directories
  - Added `exclude_dirs` set (`node_modules`, `.venv`, `venv`, `env`, `__pycache__`, `.git`, `.tox`, `dist`, `build`, `.mypy_cache`, `.ruff_cache`) to scope `validate_python_syntax` correctly
  - Applies to both backend-service and web-app (backend path) validation passes
  - Prevents false syntax errors from scanning virtual environments and build artifacts

## [0.2.13] - 2026-02-23

### Fixed

- **Validate**: `runtm validate` now respects `.runtmignore` for artifact size checks
  - Previously used a hardcoded set of 7 excluded directories, missing sandbox caches (`.npm`, `.cache`, `.local`) and other custom excludes defined in `.runtmignore`
  - Extracted shared ignore logic (`build_ignore_spec()` + `ALWAYS_EXCLUDE_PATTERNS`) so `validate` and `deploy` use identical file matching
  - Error message now correctly points users to `.runtmignore` instead of `.gitignore`

## [0.2.12] - 2026-02-22

### Changed

- **Deployment Tiers**: Simplified and upgraded machine tier lineup
  - Removed `standard` tier; tiers are now `starter`, `performance`, and `pro`
  - `starter`: 2 shared CPUs, 2GB RAM (~$15/month) — default for all projects
  - `performance`: 4 shared CPUs, 4GB RAM (~$30/month) — heavier workloads and multi-service apps
  - `pro`: 4 shared CPUs, 8GB RAM (~$55/month) — memory-intensive applications
  - Default resource limits updated to match new starter tier (2 CPUs, 2GB RAM)
- **Manifest**: Removed fullstack tier validation — all current tiers can run fullstack apps

### Fixed

- **Deployments**: Flush `is_latest=False` before promoting previous deployment on destroy to prevent unique constraint violations

## [0.2.11] - 2026-01-23

### Changed

- **Docker Template**: Lockfile validation is now skipped for docker template
  - Docker template uses bring-your-own-Dockerfile approach, so lockfile checks are not applicable
  - Validation focuses on `runtm.yaml`, `Dockerfile`, and artifact size only

## [0.2.10] - 2026-01-23

### Added

- **Docker Template**: Bring your own Dockerfile for Go, Rust, Elixir, or any language
  - `runtm init docker` scaffolds minimal `runtm.yaml` and `Dockerfile`
  - No runtime required - deploy any containerized application
  - Full template documentation with AI assistant instructions
- **JSON Output for AI Agents**: Machine-readable output for CLI commands
  - `runtm validate --json` returns structured validation results
  - `runtm deploy --json` streams NDJSON events for each deployment phase
  - Enables programmatic integration with AI coding agents

- **Local Sandboxes**: Isolated environments where AI coding agents can build software
  - `runtm start` - Start a sandbox with interactive mode/agent selection
  - `runtm attach [id]` - Attach to a sandbox (defaults to last active in terminal)
  - `runtm prompt "..."` - Send prompts to the agent in autopilot mode
  - `runtm session list` - List all sandbox sessions
  - `runtm session stop <id>` - Stop a sandbox (preserves workspace)
  - `runtm session destroy <id>` - Destroy a sandbox and delete workspace
  - `runtm session deploy` - Deploy from sandbox to live URL
- **Agent Orchestrator** (`runtm-agents`): New package for AI coding agent integration
  - Claude Code adapter with streaming JSON output parsing
  - Autopilot mode: send prompts via CLI, agent executes autonomously
  - Interactive mode: drop into sandbox shell, run agent manually
  - Session continuation support (`--continue` flag)
  - Real-time output streaming (tool use, text, errors)
- **Sandbox Package** (`runtm-sandbox`): New package for sandbox management
  - Uses Anthropic's sandbox-runtime for fast startup (<100ms)
  - OS-level isolation via bubblewrap (Linux) / seatbelt (macOS)
  - Automatic dependency installation on first run
  - Graceful fallback when ripgrep not installed
  - Custom shell prompt showing sandbox ID
  - Terminal-specific session tracking (multiple terminals, multiple sandboxes)
- **Sandbox UX Improvements**
  - Welcome banner when entering sandbox
  - Custom prompt: `[sandbox:abc123] ~/path $`
  - Exit message with next-step suggestions
  - Environment variables for scripts: `RUNTM_SANDBOX`, `RUNTM_WORKSPACE`

### Changed

- **Manifest Schema**: `runtime` field is now optional for `docker` template
- `runtm list` now also available as `runtm deployments list`
- Verbose logging now opt-in (`--verbose` flag or `RUNTM_DEBUG=1`)
- Keyring dependency now optional (falls back to file-based credential storage)

### Fixed

- Fixed indentation error in `runtm init` for docker template handling
- Fixed `runtm start` bypassing interactive menus
- Fixed `runtm attach` requiring sandbox ID (now defaults to active session)
- Fixed sandbox-runtime config format (`allowedDomains` vs `allowDomains`)
- Fixed sandbox-runtime flag (`--settings` vs `--config`)
- Fixed CLI crash when keyring package not installed

## [0.1.0] - 2025-01-01

### Added

- Initial open source release
- **CLI**: `runtm` command-line tool for deploying AI-generated code
  - `runtm init` - Scaffold from templates (backend-service, static-site, web-app)
  - `runtm run` - Run projects locally with auto-detection (uses Bun if available)
  - `runtm deploy` - Deploy to live URLs with machine tiers
  - `runtm fix` - Auto-fix common project issues (lockfiles)
  - `runtm validate` - Validate projects before deployment
  - `runtm status` - Check deployment status
  - `runtm logs` - View build, deploy, and runtime logs with search/filtering
  - `runtm list` - List all deployments
  - `runtm search` - Search deployments by description/tags
  - `runtm destroy` - Destroy deployments
  - `runtm login/logout` - Authentication management
  - `runtm secrets set/get/list/unset` - Manage environment variables
  - `runtm domain add/status/remove` - Custom domain management
  - `runtm approve` - Apply agent-proposed changes
  - `runtm admin create-token/revoke-token/list-tokens` - Self-host token management
- **API**: FastAPI control plane for deployment management
- **Worker**: Build and deploy pipeline with Fly.io provider
- **Templates**:
  - `backend-service` - Python FastAPI backend
  - `static-site` - Next.js static site
  - `web-app` - Fullstack Next.js + FastAPI
- **Features**:
  - Machine tiers (starter, standard, performance) with auto-stop
  - Environment variable management with secret redaction
  - Custom domain support with SSL certificates
  - Optional SQLite database with persistence
  - Optional authentication (web-app template)
  - Agent workflow support via `runtm.requests.yaml`
  - Lockfile validation and auto-fix

### Security

- Bearer token authentication for all API calls
- Rate limiting (10 deployments/hour per token)
- Artifact size limits (20 MB max)
- Build/deploy timeouts
- Secret redaction in logs

[Unreleased]: https://github.com/runtm-ai/runtm/compare/v0.2.20...HEAD
[0.2.20]: https://github.com/runtm-ai/runtm/compare/v0.2.19...v0.2.20
[0.2.19]: https://github.com/runtm-ai/runtm/compare/v0.2.18...v0.2.19
[0.2.18]: https://github.com/runtm-ai/runtm/compare/v0.2.17...v0.2.18
[0.2.17]: https://github.com/runtm-ai/runtm/compare/v0.2.16...v0.2.17
[0.2.16]: https://github.com/runtm-ai/runtm/compare/v0.2.15...v0.2.16
[0.2.15]: https://github.com/runtm-ai/runtm/compare/v0.2.14...v0.2.15
[0.2.14]: https://github.com/runtm-ai/runtm/compare/v0.2.13...v0.2.14
[0.2.13]: https://github.com/runtm-ai/runtm/compare/v0.2.12...v0.2.13
[0.2.12]: https://github.com/runtm-ai/runtm/compare/v0.2.11...v0.2.12
[0.2.11]: https://github.com/runtm-ai/runtm/compare/v0.1.0...v0.2.11
[0.1.0]: https://github.com/runtm-ai/runtm/releases/tag/v0.1.0
