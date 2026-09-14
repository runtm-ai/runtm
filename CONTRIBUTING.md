# Contributing to Runtm

Thanks for your interest in contributing to Runtm – sandboxes where coding agents build and deploy.

[runtm.com](https://runtm.com) | [app.runtm.com](https://app.runtm.com) | [Discord](https://discord.com/invite/JUuCkUKc)

## Design Principles

These principles guide all decisions. Keep them in mind when contributing:

1. **Simplify to the most basic primitives** - Remove complexity, not add it
2. **Make it extremely easy to use** - One command should do the job
3. **Make it versatile and scalable** - Entire ecosystems can be built on top
4. **Optimize for tight, closed feedback loops** - Fast iteration over perfect planning
5. **Design for agents first, then for humans** - AI should be the primary user
6. **Agents propose, humans set guardrails** - Freedom with governance
7. **Make behavior explicit, observable, and reproducible** - No magic

## For AI Assistants (Cursor, Claude Code, etc.)

If you're using an AI assistant to contribute, the `.cursor/rules/` directory contains comprehensive context:

| File | Purpose |
|------|---------|
| `.cursor/rules/system-instructions.mdc` | Architecture, types, CLI commands, API endpoints |
| `.cursor/rules/templates.mdc` | Template creation and maintenance guidelines |

These files are automatically loaded by Cursor and provide essential context for AI-assisted development. You can replicate them to fit with Claude Code: CLAUDE.md.

## Development Setup

### Prerequisites

- Python 3.11+ or uv
- Docker & Docker Compose
- Git
- Fly.io account + API token only when testing real deployments

### Getting Started

1. Fork the repository
2. Clone your fork:
   ```bash
   git clone https://github.com/YOUR_USERNAME/runtm.git
   cd runtm
   ```

3. Create the local development environment:
   ```bash
   ./.runtm/setup.local.sh
   ```

   This copies `.env.local.example` when needed, allocates stable per-workspace
   ports, starts Postgres/Redis through Docker Compose, applies migrations from
   `.venv`, and runs `runtm-dev doctor` against a temporary host API process.
   If a remembered port is later taken by another process, setup allocates a new
   free port window and rewrites `.env` plus `.runtm/ports.json`.

4. Refresh Python packages manually when needed:
   ```bash
   ./scripts/dev.sh setup
   ```

   Or manually with **uv** (recommended):
   ```bash
   uv pip install -e packages/shared[dev]
   uv pip install -e packages/api[dev]
   uv pip install -e packages/worker[dev]
   uv pip install -e "packages/cli[dev,sandbox]"
   uv pip install -e packages/sandbox
   uv pip install -e packages/agents
   ```

   Or with **pip**:
   ```bash
   pip install -e packages/shared[dev]
   pip install -e packages/api[dev]
   pip install -e packages/worker[dev]
   pip install -e "packages/cli[dev,sandbox]"
   pip install -e packages/sandbox
   pip install -e packages/agents
   ```

5. Set up pre-commit hooks manually when needed:
   ```bash
   pip install pre-commit
   pre-commit install
   ```

### Development CLI (`runtm-dev`)

After running `./scripts/dev.sh setup`, use `runtm-dev` for development:

```bash
# Activate venv (or add .venv/bin to PATH)
source .venv/bin/activate

# Use the development CLI
runtm-dev start                    # Start a sandbox session
runtm-dev prompt "Build an API"    # Send prompt to agent
runtm-dev list                     # List sessions
```

**Why two CLIs?**
| CLI | Source | Includes |
|-----|--------|----------|
| `runtm` | PyPI (`pip install runtm`) | Core CLI only |
| `runtm-dev` | Local `.venv/` | CLI + sandbox + agents + all deps |

The setup script installs everything (Python packages, Bun, sandbox-runtime, Claude CLI), so `runtm-dev start` just works.

### Running Local Services

Start the fast local stack. Docker runs only Postgres and Redis; API and worker
run directly from `.venv`:

```bash
./scripts/dev.sh run-local
```

The existing full Docker stack remains available when testing Dockerfiles or
container parity:

```bash
./scripts/dev.sh up
./scripts/dev.sh rebuild-docker
```

Configure CLI to use local API:

```bash
export RUNTM_API_URL="$(python -c 'import json; print(json.load(open(".runtm/ports.json"))["services"]["api"]["url"])')"
export RUNTM_API_KEY=dev-token-change-in-production
```

The generated `.runtm/ports.json` file records the API, Postgres, and Redis
ports for this workspace. This keeps multiple worktrees or AI agents from
colliding on fixed ports, and gives agents the exact setup/run/teardown
commands to use. Run `./scripts/dev.sh doctor-local` when you want a pass/fail
check for generated ports, `.env`, Docker project ownership, and port
conflicts. Run `./scripts/dev.sh diagnose-env` when you need a masked summary
of the local environment and Compose state.

### Dev Script Commands

The `./scripts/dev.sh` helper automatically loads your `.env` file:

| Command | Description |
|---------|-------------|
| `./scripts/dev.sh setup-local` | Create isolated local dev environment for this workspace |
| `./scripts/dev.sh teardown-local` | Stop this workspace's local services |
| `./scripts/dev.sh run-local` | Start API + worker from `.venv` and follow logs |
| `./scripts/dev.sh setup` | Install all packages in dev mode |
| `./scripts/dev.sh up` | Start full Docker stack including API + worker |
| `./scripts/dev.sh deps-up` | Start local Postgres + Redis only |
| `./scripts/dev.sh up-docker` | Alias for `up` |
| `./scripts/dev.sh down` | Stop local services |
| `./scripts/dev.sh restart` | Restart full Docker stack |
| `./scripts/dev.sh rebuild-docker` | Rebuild API/worker images and restart full Docker stack |
| `./scripts/dev.sh logs [service]` | View full Docker stack logs |
| `./scripts/dev.sh deps-logs` | View Postgres/Redis logs |
| `./scripts/dev.sh logs-docker [service]` | Alias for `logs` |
| `./scripts/dev.sh doctor` | Check local CLI/API/sandbox setup |
| `./scripts/dev.sh doctor-local` | Validate generated local ports, `.env`, and Docker project state |
| `./scripts/dev.sh diagnose-env` | Print masked local environment and Compose diagnostics |
| `./scripts/dev.sh ports` | Show generated local service ports |
| `./scripts/dev.sh test` | Run tests |
| `./scripts/dev.sh lint` | Run linter |
| `./scripts/dev.sh format` | Format code |
| `./scripts/dev.sh check` | Run lint and format checks without modifying files |

## Project Structure

```
packages/
  shared/     # Canonical contracts: manifest schema, types, errors
  sandbox/    # Local sandbox runtime (OS-level isolation)
  agents/     # AI coding agent adapters (Claude Code, Codex, etc.)
  api/        # FastAPI control plane
  worker/     # Build + deploy worker (Fly.io provider)
  cli/        # Python CLI (Typer)

templates/
  backend-service/  # Python FastAPI backend (API, webhooks, agents)
  static-site/      # Next.js static site (landing pages, docs)
  web-app/          # Fullstack Next.js + FastAPI (dashboards, apps)

infra/
  docker-compose.yml  # Local development stack
```

### Package Responsibilities

| Package | Purpose | Key Principle |
|---------|---------|---------------|
| `shared` | Canonical contracts only | Types/schemas/errors that other packages import |
| `sandbox` | Local sandbox runtime | OS-level isolation for AI agents |
| `agents` | AI agent adapters | Wraps Claude Code, Codex, etc. |
| `api` | HTTP layer + orchestration | No business logic in routes - use services |
| `worker` | Build/deploy pipeline | Provider abstraction for Fly.io/Cloud Run |
| `cli` | User interface | Wraps API client, never contains business logic |

## Code Style

We use [Ruff](https://github.com/astral-sh/ruff) for linting and formatting:

```bash
# Check for issues
./scripts/dev.sh lint
# or: ruff check .

# Auto-fix issues
ruff check --fix .

# Format code
./scripts/dev.sh format
# or: ruff format .
```

### Code Quality Standards

- Structured logging everywhere, no print statements
- Clean error messages that explain how to recover
- Idempotency: retries create same result
- Type hints on all functions
- Real tests for critical paths

## Testing

Run tests with pytest:

```bash
# All tests via dev script
./scripts/dev.sh test

# All tests directly
pytest

# Specific package
pytest packages/api/tests
pytest packages/shared/tests
pytest packages/cli/tests

# With coverage
pytest --cov=runtm_api packages/api/tests
```

## Architecture Guidelines

### Key Files

| File | Purpose |
|------|---------|
| `packages/shared/runtm_shared/manifest.py` | Deployment manifest schema (Pydantic) |
| `packages/shared/runtm_shared/types.py` | State machine, tiers, auth types |
| `packages/shared/runtm_shared/errors.py` | Error hierarchy |
| `packages/api/runtm_api/routes/deployments.py` | Deployment API endpoints |
| `packages/worker/runtm_worker/jobs/deploy.py` | Build/deploy job logic |
| `packages/worker/runtm_worker/providers/fly.py` | Fly.io provider implementation |
| `packages/cli/runtm_cli/main.py` | CLI command definitions |

### Adding a New CLI Command

1. Create command function in `packages/cli/runtm_cli/commands/`
2. Export from `packages/cli/runtm_cli/commands/__init__.py`
3. Register in `packages/cli/runtm_cli/main.py`
4. Add tests in `packages/cli/tests/`

### Adding an API Endpoint

1. Add route in `packages/api/runtm_api/routes/`
2. Keep route handlers thin – delegate to services
3. Use Pydantic models for request/response
4. Add tests in `packages/api/tests/`

### Adding a Provider

1. Create provider in `packages/worker/runtm_worker/providers/`
2. Implement the `DeploymentProvider` interface
3. Register in provider factory

### Modifying Shared Types

1. Edit in `packages/shared/runtm_shared/`
2. Update all consuming packages
3. Types flow: shared → api, worker, cli

### Adding/Modifying Templates

See `.cursor/rules/templates.mdc` for comprehensive template guidelines.

## Pull Request Process

1. Create a feature branch from `main`:
   ```bash
   git checkout -b feature/my-feature
   ```

2. Make your changes and ensure:
   - Tests pass: `./scripts/dev.sh test`
   - Linting passes: `./scripts/dev.sh lint`
   - Code is formatted: `./scripts/dev.sh format`

3. Commit with a clear message:
   ```bash
   git commit -m "feat: add new deployment status endpoint"
   ```

4. Push and open a Pull Request

## Commit Message Format

We follow [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation changes
- `chore:` Maintenance tasks
- `refactor:` Code refactoring
- `test:` Test additions/changes

## Questions?

Open a [GitHub issue](https://github.com/runtm-ai/runtm/issues) or join our [Discord](https://discord.com/invite/JUuCkUKc) for questions about contributing.
