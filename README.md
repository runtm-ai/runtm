# Runtm

[![License: AGPL v3](https://img.shields.io/badge/Server-AGPLv3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![License: Apache 2.0](https://img.shields.io/badge/CLI-Apache%202.0-green.svg)](https://opensource.org/licenses/Apache-2.0)
[![License: MIT](https://img.shields.io/badge/Templates-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Discord](https://img.shields.io/discord/1342243238748225556?logo=discord&logoColor=white&color=7289DA)](https://discord.com/invite/JUuCkUKc)

Open-source sandboxes where coding agents build and deploy.

Spin up isolated environments where Claude Code, Cursor, Codex, and other agents code and ship software. With live URLs, logs, and previews.

**[Website](https://runtm.com)** · **[Docs](https://docs.runtm.com)** · **[Get Started](https://app.runtm.com)**

## Demo

https://github.com/user-attachments/assets/8d6d5ab8-a5c4-4a3d-8ef1-5d20b67ed3ee

## Why Runtm?

- **Sandboxes for yolo agents** – Let agents code with full permissions in isolated environments. No risk to your machine or cloud.
- **Run any coding agent** – Claude Code, Cursor, Codex, Gemini CLI, and more. Bring your favorite.
- **Real URLs instantly** – Agents deploy to live HTTPS endpoints. Test, share, and iterate.
- **Logs and observability** – See what your agent built, debug issues, fix and redeploy.

## Quick Start

```bash
# Install
uv tool install runtm

# Start a local sandbox session
runtm session start

# Your agent builds inside the sandbox...

# Deploy to a live URL
runtm session deploy
```

You get a live HTTPS endpoint on auto-stopping infrastructure. Machines spin down when idle and wake up on traffic.

## Local Sandbox Sessions

Run AI agents in isolated local environments with OS-level sandboxing:

```bash
# Start a sandbox (auto-installs deps on first run)
runtm session start

# Start with a template
runtm session start --template web-app

# Use a different agent
runtm session start --agent codex

# List all sandboxes
runtm session list

# Reattach to a sandbox
runtm session attach sbx_abc123

# Deploy from sandbox to live URL
runtm session deploy
```

**What you get:**
- **OS-level isolation** – Uses bubblewrap (Linux) or seatbelt (macOS) for fast, secure sandboxing
- **Instant startup** – Sandboxes start in <100ms, no containers needed
- **Multi-agent support** – Works with Claude Code, Codex, Gemini CLI, and more
- **Persistent workspaces** – Stop and resume sessions, files preserved

## How It Works

```
┌────────────┐     ┌─────────┐     ┌─────────┐     ┌──────────┐
│ Your Agent │ ──▶ │ Sandbox │ ──▶ │  Runtm  │ ──▶ │ Live URL │
└────────────┘     └─────────┘     └─────────┘     └──────────┘
```

1. **Spin up a sandbox** – Your agent gets an isolated workspace
2. **Agent builds** – Full permissions to code, install deps, run tests
3. **Deploy** – One command to a real URL with logs and previews
4. **Iterate** – Agent can see logs, fix bugs, and redeploy

## Installation

The pip CLI (`runtm`) for local sandboxes, scaffolding, and deploys:

```bash
# Recommended
uv tool install runtm

# Alternative
pipx install runtm

# Or with pip
pip install runtm
```

The Go CLI (`runtm-api`) is a separate, lighter tool for AI coding agents driving the hosted Cloud API:

```bash
curl -fsSL https://runtm.com/install | bash
```

It is JSON-in / JSON-out, has stable exit codes, and ships with embedded skill files for Claude Code, Cursor, and Codex. See [Agent CLI docs](https://docs.runtm.com/cloud-api/agent-cli) and the [`packages/agent/`](packages/agent/) source for details. The pip and Go CLIs share the same API key.

## Commands

| Command | Description |
|---------|-------------|
| `runtm session start` | Start a new sandbox session |
| `runtm session list` | List all sandboxes |
| `runtm session attach <id>` | Reattach to a sandbox |
| `runtm session stop <id>` | Stop a sandbox (preserves files) |
| `runtm session destroy <id>` | Destroy sandbox and delete files |
| `runtm session deploy` | Deploy from sandbox to live URL |
| `runtm init` | Initialize a new project |
| `runtm deploy` | Deploy to a live URL |
| `runtm logs <id>` | View build, deploy, and runtime logs |
| `runtm status <id>` | Check deployment status |
| `runtm destroy <id>` | Tear down a deployment |

See the [CLI docs](https://docs.runtm.com/cli/overview) for the full reference.

## Self-Hosting

Runtm can be fully self-hosted. See the [self-hosting guide](https://docs.runtm.com/self-hosting/overview).

```bash
git clone https://github.com/runtm-ai/runtm.git
cd runtm
cp infra/local.env.example .env

# Install packages (includes sandbox and agents)
./scripts/dev.sh setup

# Start local services
docker compose -f infra/docker-compose.yml up -d

# Use the development CLI
runtm-dev start                    # Start a sandbox session
runtm-dev prompt "Build an API"    # Send prompt to agent
```

**Note:** Use `runtm-dev` (not `runtm`) when self-hosting. The dev CLI includes sandbox/agents packages.

## Project Structure

```
packages/
  shared/     # Types, manifest schema, errors
  sandbox/    # Local sandbox runtime (OS-level isolation)
  agents/     # AI coding agent adapters (Claude Code, Codex, etc.)
  api/        # FastAPI control plane
  worker/     # Build + deploy pipeline
  cli/        # Python CLI (Typer) - `runtm` (local sandbox + deploy)
  agent/      # Go CLI (Cobra) - `runtm-api` (hosted Cloud API for AI agents)

templates/    # Starter projects (backend, static, fullstack)
```

## Documentation

Full docs at [docs.runtm.com](https://docs.runtm.com):

- [Quickstart](https://docs.runtm.com/quickstart)
- [CLI Reference](https://docs.runtm.com/cli/overview)
- [API Reference](https://docs.runtm.com/api/overview)
- [Self-Hosting](https://docs.runtm.com/self-hosting/overview)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and guidelines.

## License

| Component | License |
|-----------|---------|
| Server (api, worker, infra) | [AGPLv3](packages/api/LICENSE) |
| CLI, Sandbox, Shared | [Apache-2.0](packages/cli/LICENSE) |
| Templates | [MIT](templates/LICENSE) |

## Support

[Open an issue](https://github.com/runtm-ai/runtm/issues) or join our [Discord](https://discord.com/invite/JUuCkUKc).
