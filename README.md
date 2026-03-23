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

```bash
# Recommended
uv tool install runtm

# Alternative
pipx install runtm

# Or with pip
pip install runtm
```

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

## Run with Docker (any OS)

Get the full stack running in under 5 minutes on **Linux, macOS, or Windows**.
The only prerequisite is [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker Engine + Compose on Linux).

```bash
git clone https://github.com/runtm-ai/runtm.git
cd runtm
cp infra/local.env.example .env   # defaults to DEPLOY_PROVIDER=local — no accounts needed
make dev                           # builds images, starts Postgres, Redis, API, and worker
```

That's it. The API is live at **http://localhost:8000**.

```bash
curl http://localhost:8000/health            # quick check
curl http://localhost:8000/health/detailed   # Postgres, Redis, queue depth
```

### Common commands

| Command | What it does |
|---------|-------------|
| `make dev` | Start the stack with hot-reload (code changes apply in ~3 s) |
| `make test` | Run the full test suite inside Docker |
| `make lint` | Lint with ruff inside Docker |
| `make logs` | Tail all service logs (`make logs SVC=api` to filter) |
| `make down` | Stop everything |
| `make db-reset` | Wipe volumes and recreate from scratch |

> **Windows without `make`?** Use the PowerShell helper instead:
> ```powershell
> .\scripts\dev.ps1 dev      # same as make dev
> .\scripts\dev.ps1 test     # same as make test
> ```

### What's running

```
┌──────────┐   ┌───────┐   ┌─────────────────────┐   ┌────────────┐
│ Postgres │   │ Redis │   │ API (uvicorn+reload) │   │   Worker   │
│ :5432    │   │ :6379 │   │ :8000                │   │ (watchfiles│
└──────────┘   └───────┘   └─────────────────────┘   │  auto-     │
                                                      │  restart)  │
                                                      └────────────┘
```

Source code is bind-mounted so edits to `packages/api/`, `packages/worker/`, or `packages/shared/` are picked up automatically.

### Networking and deployed app URLs

Everything runs on `localhost` -- **no ngrok, Cloudflare Tunnel, or port forwarding is required** to develop, test, or run the full workflow locally.

When you deploy an app with the `local` provider, it starts as a Docker container on your machine at `http://localhost:9000` (ports 9000-9999 are allocated automatically). This is perfect for development and personal use.

If you need a **public URL** to share with others:

| Goal | Solution |
|------|----------|
| Quick sharing / demos | [ngrok](https://ngrok.com), [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/), or [tailscale funnel](https://tailscale.com/kb/1223/funnel) pointed at your local port |
| Permanent self-hosting | Deploy on a VPS with `docker-compose.prod.yml` -- Caddy provides automatic HTTPS (see [Production self-hosting](#production-self-hosting)) |
| Managed hosting | Use `DEPLOY_PROVIDER=fly` with a [Fly.io](https://fly.io) account -- apps get public URLs automatically |

### Environment variables

The `.env` file controls the stack. The only **required** variable is:

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `RUNTM_API_SECRET` | **Yes** | — | API authentication secret |
| `DEPLOY_PROVIDER` | No | `local` | `local` (no accounts) or `fly` (Fly.io) |
| `FLY_API_TOKEN` | Only if `fly` | — | Fly.io personal access token |
| `FLY_ORG` | Only if `fly` | `personal` | Fly.io org slug |

All other variables (`DATABASE_URL`, `REDIS_URL`, etc.) are set by the compose file and should not need changing for local development.

### Production self-hosting

For self-hosting with HTTPS and managed databases, see `infra/docker-compose.prod.yml`:

```bash
cp infra/local.env.prod.example .env.prod
# Edit .env.prod: set DATABASE_URL, REDIS_URL, RUNTM_DOMAIN, RUNTM_API_SECRET
docker compose -f infra/docker-compose.prod.yml --env-file .env.prod up -d
```

Caddy handles automatic TLS via Let's Encrypt. See the [self-hosting guide](https://docs.runtm.com/self-hosting/overview) for details.

## Self-Hosting (bare metal)

If you prefer running without Docker (e.g., for IDE debugging), you can install packages directly:

```bash
git clone https://github.com/runtm-ai/runtm.git
cd runtm
cp infra/local.env.example .env
./scripts/dev.sh setup    # creates venv, pip installs all packages
./scripts/dev.sh up       # starts Postgres + Redis via Docker, API via uvicorn
```

> **Note:** The bare-metal path requires Python 3.12+, bash, and still uses Docker for Postgres/Redis.

## Project Structure

```
packages/
  shared/     # Types, manifest schema, errors
  sandbox/    # Local sandbox runtime (OS-level isolation)
  agents/     # AI coding agent adapters (Claude Code, Codex, etc.)
  api/        # FastAPI control plane
  worker/     # Build + deploy pipeline
  cli/        # Python CLI (Typer)

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
