# runtm

CLI for Runtm – sandboxes where AI agents build and deploy.

**Website:** [runtm.com](https://runtm.com) · **Docs:** [docs.runtm.com](https://docs.runtm.com) · **Sign up:** [app.runtm.com](https://app.runtm.com)

## Installation

**Recommended (uv):**
```bash
uv tool install runtm
```

**Alternative (pipx):**
```bash
pipx install runtm
```

**From PyPI (pip):**
```bash
pip install runtm
```

### With Sandbox Support

To use local sandboxes with AI agents:

```bash
pip install runtm[sandbox]
```

### Upgrading

```bash
# Upgrade to latest version (uv)
uv tool upgrade runtm

# Or force reinstall
uv tool install runtm --force

# With pipx
pipx upgrade runtm

# With pip
pip install --upgrade runtm
```

## Quick Start

```bash
# 1. Authenticate with Runtm
runtm login

# 2. Start a sandbox and build with AI
runtm start
runtm prompt "Build a REST API with SQLite"

# 3. Deploy to a live URL
runtm deploy
```

You get a live HTTPS endpoint. Machines auto-stop when idle and wake on traffic.

## Commands

### Sandbox Commands

| Command | Description |
|---------|-------------|
| `runtm start` | Start a sandbox session (interactive menu) |
| `runtm prompt "..."` | Send a prompt to the agent (autopilot mode) |
| `runtm attach [id]` | Attach to a sandbox (defaults to active) |
| `runtm session list` | List all sandbox sessions |
| `runtm session stop <id>` | Stop a session (preserves workspace) |
| `runtm session destroy <id>` | Destroy a session and delete workspace |
| `runtm session deploy` | Deploy from sandbox to live URL |

### Project Commands

| Command | Description |
|---------|-------------|
| `runtm init [template]` | Initialize from template (backend-service, web-app, static-site) |
| `runtm run` | Run project locally (auto-detects runtime) |
| `runtm validate` | Validate project before deployment |
| `runtm fix` | Auto-fix common issues (lockfiles) |
| `runtm deploy [path]` | Deploy project to a live URL |

### Deployment Commands

| Command | Description |
|---------|-------------|
| `runtm status <id>` | Show deployment status |
| `runtm logs <id>` | Show logs (build, deploy, runtime) |
| `runtm list` | List all deployments |
| `runtm search <query>` | Search deployments by description/tags |
| `runtm destroy <id>` | Destroy a deployment |

### Configuration Commands

| Command | Description |
|---------|-------------|
| `runtm config set/get/list` | Manage CLI configuration |
| `runtm secrets set/get/list/unset` | Manage environment secrets |
| `runtm domain add/status/remove` | Manage custom domains |
| `runtm approve` | Apply agent-proposed changes |

### Authentication Commands

| Command | Description |
|---------|-------------|
| `runtm login` | Authenticate with Runtm API |
| `runtm logout` | Remove saved credentials |
| `runtm doctor` | Check CLI setup and diagnose issues |
| `runtm version` | Show CLI version |

## Sandbox Sessions

Start isolated environments where AI agents can build software:

```bash
# Start with interactive menu
runtm start

# Or go directly to autopilot mode
runtm start --autopilot

# Send prompts to the agent
runtm prompt "Build a todo API with SQLite"
runtm prompt --continue "Add authentication"

# Attach to see what's happening
runtm attach

# List all sessions
runtm session list
```

### Modes

- **Autopilot**: Agent runs autonomously, control via `runtm prompt`
- **Interactive**: Drop into sandbox shell, control agent manually

### Available Agents

- `claude-code` - Anthropic's Claude Code (recommended)
- `codex` - OpenAI's Codex CLI
- `gemini` - Google's Gemini CLI

## Authentication

Get your free API key at **[app.runtm.com](https://app.runtm.com)**. The CLI will prompt you to authenticate on first use.

```bash
# Manual login
runtm login

# Login with token directly
runtm login --token runtm_sk_xxx

# Check auth status
runtm doctor

# Logout
runtm logout
```

**Token storage:**
- Primary: `~/.runtm/credentials` file (0o600 permissions)
- Optional: System keychain (if `keyring` package installed)

**Environment variable override:**
```bash
export RUNTM_API_KEY=runtm_sk_xxx  # Overrides stored token
```

## Configuration

```bash
# Set API URL (for self-hosting)
runtm config set api_url=https://self-hosted.example.com/api

# Get a config value
runtm config get api_url

# List all config values
runtm config list

# Reset to defaults
runtm config reset
```

**Config file:** `~/.runtm/config.yaml`

**Environment variables:**
- `RUNTM_API_URL` - API endpoint (overrides config)
- `RUNTM_API_KEY` - API key (overrides stored token)
- `RUNTM_DEBUG` - Enable debug logging

## Secrets Management

Manage environment variables for deployments:

```bash
# Set secrets
runtm secrets set DATABASE_URL=postgres://...
runtm secrets set API_KEY=sk-xxx

# List secrets
runtm secrets list

# Get a secret value
runtm secrets get DATABASE_URL

# Remove a secret
runtm secrets unset OLD_KEY
```

Secrets are stored in `.env.local` (gitignored) and injected at deploy time.

## Troubleshooting

```bash
# Check CLI setup and diagnose issues
runtm doctor
```

Example output:
```
runtm v0.2.7
  API URL:      https://app.runtm.com/api
  Auth storage: keychain (api_token@app.runtm.com)
  Auth status:  ✓ Authenticated as user@example.com
  Connectivity: ✓ API reachable (142ms)

  Ready to deploy! Run: runtm init
```

## Machine Tiers

All deployments use **auto-stop** for cost savings (machines stop when idle and start automatically on traffic).

| Tier | CPUs | Memory | Est. Cost | Use Case |
|------|------|--------|-----------|----------|
| **starter** (default) | 1 shared | 256MB | ~$2/month* | Simple tools, APIs |
| **standard** | 1 shared | 512MB | ~$5/month* | Most workloads |
| **performance** | 2 shared | 1GB | ~$10/month* | Full-stack apps |

*Costs are estimates for 24/7 operation. With auto-stop, costs are much lower for low-traffic services.

## Deployment

```bash
# Deploy to a live URL (uses starter tier by default)
runtm deploy

# Deploy with a specific tier
runtm deploy --tier standard
runtm deploy --tier performance

# Check deployment status
runtm status dep_abc123

# View logs
runtm logs dep_abc123
```

### Redeployment (CI/CD)

Runtm supports automatic redeployment based on the project name in `runtm.yaml`:

```bash
# First deploy - creates new deployment
runtm deploy                   # → v1, creates new URL

# Fix a bug, then redeploy - updates existing
runtm deploy                   # → v2, same URL, updated code

# Force a completely new deployment
runtm deploy --new             # → v1, new deployment, new URL
```

## Logs

```bash
# All logs (build + deploy + recent runtime)
runtm logs dep_abc123

# Filter by log type
runtm logs dep_abc123 --type runtime
runtm logs dep_abc123 --type build

# More runtime log lines
runtm logs dep_abc123 --lines 100

# Search logs
runtm logs dep_abc123 --search "error"
runtm logs dep_abc123 --search "error,warning,timeout"  # OR logic

# Pipe to grep (Heroku-style)
runtm logs dep_abc123 --raw | grep "error"

# JSON output for AI agents
runtm logs dep_abc123 --json
```

## Development

```bash
# Install in editable mode with sandbox support
pip install -e ".[dev,sandbox]"
pip install -e ../sandbox
pip install -e ../agents

# Use the development CLI (avoids conflicts with PyPI version)
runtm-dev start                    # Start sandbox session
runtm-dev prompt "Build an API"    # Send prompt to agent
runtm-dev session list             # List sessions

# Configure CLI to use local API (add to ~/.zshrc or ~/.bashrc)
export RUNTM_API_URL=http://localhost:8000
export RUNTM_API_KEY=dev-token-change-in-production

# Run tests
pytest
```

### `runtm` vs `runtm-dev`

| CLI | Source | Use Case |
|-----|--------|----------|
| `runtm` | PyPI (`pip install runtm`) | Production use |
| `runtm-dev` | Local `.venv/` | Development (includes sandbox/agents) |

If you have the PyPI version installed globally, use `runtm-dev` to ensure you're running your local development code with full sandbox support.
