# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What is Runtm?

Open-source sandboxes where coding agents build and deploy. Spin up isolated environments where Claude Code, Cursor, Codex, and other agents code and ship software with live URLs, logs, and previews.

## Design Principles

1. **Simplify to the most basic primitives** – Remove complexity, not add it
2. **Make it extremely easy to use** – One command should do the job
3. **Design for agents first, then for humans** – AI should be the primary user
4. **Make behavior explicit, observable, and reproducible** – No magic

## Development Commands

```bash
# Setup
./.runtm/setup.local.sh             # One-command local setup for this workspace
./scripts/dev.sh setup-local        # Same setup through the dev helper
./scripts/dev.sh setup              # Refresh Python packages in dev mode

# Development CLI (use runtm-dev, not runtm)
source .venv/bin/activate
runtm-dev start                     # Start sandbox session (autopilot mode)
runtm-dev start --interactive       # Start sandbox session (interactive mode)
runtm-dev prompt "Build an API"     # Send prompt to agent in sandbox
runtm-dev list                      # List sessions

# Run local services
./scripts/dev.sh run-local          # Start API + worker from .venv with reload
./scripts/dev.sh deps-up            # Start Postgres + Redis only
./scripts/dev.sh up                 # Start full Docker stack
./scripts/dev.sh down               # Stop services
./scripts/dev.sh up-docker          # Alias for full Docker stack
./scripts/dev.sh rebuild-docker     # Rebuild Docker API/worker images
./scripts/dev.sh teardown-local     # Stop this workspace's isolated stack

# Testing & Quality
./scripts/dev.sh test               # Run all tests
pytest packages/api/tests           # Test specific package
pytest packages/api/tests/test_foo.py::test_specific  # Single test
./scripts/dev.sh lint               # Check style (ruff)
./scripts/dev.sh format             # Fix style (ruff format)
./scripts/dev.sh check              # Run all checks (REQUIRED before committing)

# View logs
./scripts/dev.sh logs               # Full Docker stack logs
./scripts/dev.sh deps-logs          # Postgres + Redis logs
./scripts/dev.sh logs worker        # Specific Docker service log

# Configure CLI for local API
./scripts/dev.sh ports              # Show generated API/Postgres/Redis ports
./scripts/dev.sh doctor-local       # Validate generated ports/env/Docker state
./scripts/dev.sh diagnose-env       # Print masked local diagnostics
export RUNTM_API_URL=$(python -c 'import json; print(json.load(open(".runtm/ports.json"))["services"]["api"]["url"])')
export RUNTM_API_KEY=dev-token-change-in-production
```

`.runtm/ports.json` is the source of truth for generated local ports and run commands. If a port conflict appears, rerun `./.runtm/setup.local.sh`; it will keep workspace-owned ports or allocate a new free window. Use `./scripts/dev.sh doctor-local` before a PR when local setup behavior changed.

**Note:** Use `runtm-dev` (not `runtm`) when developing locally. The dev CLI includes sandbox/agents packages that aren't in the PyPI release.

## Architecture

```
packages/
  shared/     # Canonical contracts: types, manifest schema, errors
  sandbox/    # Local sandbox runtime (OS-level isolation)
  agents/     # AI coding agent adapters (Claude Code, Codex, etc.)
  api/        # FastAPI control plane (deployments, auth, policy)
  worker/     # Build + deploy pipeline (Fly.io provider)
  cli/        # Python CLI (Typer) - the primary DX

templates/    # Starter projects (backend-service, static-site, web-app, docker)
infra/        # Docker compose for local development
```

**Package responsibilities:**
- `shared` – Types/schemas/errors that other packages import. Types flow: shared → api, worker, cli
- `sandbox` – OS-level isolation for AI agents. Uses bubblewrap (Linux) or seatbelt (macOS)
- `agents` – Adapters for AI coding agents (Claude Code, Codex, Gemini). Wraps CLI tools with streaming output
- `api` – HTTP layer + orchestration. No business logic in routes - use services
- `worker` – Build/deploy pipeline. Provider abstraction for Fly.io/Cloud Run
- `cli` – Wraps API client, never contains business logic

## Key Files

| File | Purpose |
|------|---------|
| `packages/shared/runtm_shared/manifest.py` | Manifest Pydantic models |
| `packages/shared/runtm_shared/types.py` | State machine, tiers, auth types |
| `packages/shared/runtm_shared/errors.py` | Error hierarchy |
| `packages/api/runtm_api/routes/deployments.py` | Deployment API endpoints |
| `packages/worker/runtm_worker/jobs/deploy.py` | Build/deploy job logic |
| `packages/worker/runtm_worker/providers/fly.py` | Fly.io provider |
| `packages/cli/runtm_cli/main.py` | CLI command definitions |

## Deployment State Machine

```
queued → building → deploying → ready
    ↘      ↘           ↘
       failed ←────────────┘
         ↓
      destroyed
```

## Adding Code

### CLI Command
1. Create command in `packages/cli/runtm_cli/commands/`
2. Export from `commands/__init__.py`
3. Register in `main.py`
4. Add tests in `packages/cli/tests/`

### API Endpoint
1. Add route in `packages/api/runtm_api/routes/`
2. Keep route handlers thin – delegate to services
3. Use Pydantic models for request/response
4. Add tests in `packages/api/tests/`

### Provider
1. Create in `packages/worker/runtm_worker/providers/`
2. Implement the `DeploymentProvider` interface
3. Register in provider factory

### Shared Types
1. Edit in `packages/shared/runtm_shared/`
2. Update all consuming packages

## Plans

Store implementation plans in `.plans/PLAN_feature_name.md`. Rename to `PLAN_feature_name_DONE.md` when complete.

## Code Standards

- Structured logging everywhere – No print statements
- Type hints on all functions
- Idempotency – Retries create same result
- Clean error messages that explain how to recover

**IMPORTANT:** After making any code changes, always run:
```bash
./scripts/dev.sh check
```
This runs lint and format checks. Fix any issues before committing.
