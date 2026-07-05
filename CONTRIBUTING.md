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

- Python 3.11+ (the `api` and `worker` packages require >= 3.11)
- Docker & Docker Compose
- Git
- Fly.io account + API token (for deployment testing)

### Getting Started

1. Fork the repository
2. Clone your fork:
   ```bash
   git clone https://github.com/YOUR_USERNAME/runtm.git
   cd runtm
   ```

3. Copy environment template and configure:
   ```bash
   cp infra/local.env.example .env
   # Edit .env and add your FLY_API_TOKEN
   ```

4. Install packages using the dev script:
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

5. Set up pre-commit hooks:
   ```bash
   pip install pre-commit
   pre-commit install
   ```

6. Verify your setup:
   ```bash
   ./scripts/dev.sh doctor
   ```

   The doctor checks your Python version, `.env` configuration,
   `FLY_API_TOKEN`, Docker, sandbox dependencies, and whether local
   services are healthy — with a hint for fixing anything that fails.
   Use `runtm-dev doctor --json` for machine-readable output.

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

Start the full local stack (API, worker, database, Redis):

```bash
./scripts/dev.sh up
```

Configure CLI to use local API:

```bash
export RUNTM_API_URL=http://localhost:8000
export RUNTM_API_KEY=dev-token-change-in-production
```

### Dev Script Commands

The `./scripts/dev.sh` helper automatically loads your `.env` file:

| Command | Description |
|---------|-------------|
| `./scripts/dev.sh setup` | Install all packages in dev mode |
| `./scripts/dev.sh up` | Start local services (auto-loads .env) |
| `./scripts/dev.sh down` | Stop local services |
| `./scripts/dev.sh restart` | Restart services (auto-loads .env) |
| `./scripts/dev.sh rebuild` | Rebuild images and restart (after code changes) |
| `./scripts/dev.sh logs [service]` | View logs (e.g., `logs worker`) |
| `./scripts/dev.sh test` | Run tests |
| `./scripts/dev.sh lint` | Run linter |
| `./scripts/dev.sh format` | Format code |

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
