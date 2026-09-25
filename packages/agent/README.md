# runtm (agent CLI)

A small Go CLI plus AI agent skills that let coding agents (Claude Code, Cursor, Codex, etc.) drive Runtm Cloud programmatically. Install it, set an API key, and tell your local agent:

> _"Use runtm to launch a session against our Internal API template, fix the auth middleware, and open a PR."_

The agent reads the bundled `SKILL.md`, which points it at the documentation index (https://docs.runtm.com/llms.txt) for the method and every recipe, and executes the `runtm-api` commands it finds there.

This package is intentionally separate from the pip `runtm` CLI under [`packages/cli/`](../cli/). The pip CLI is a developer-facing tool with interactive prompts, local sandboxes, and deploy workflows. The Go CLI in this directory is a **thin HTTP wrapper** designed for AI agents: stdlib JSON in, stdlib JSON out, predictable exit codes, no interactive UI.

## Install

```bash
curl -fsSL https://runtm.com/install | bash
```

Downloads a pre-built binary for your platform (macOS / Linux × amd64 / arm64), verifies its SHA-256 checksum, and installs `runtm-api` to `/usr/local/bin` (override with `RUNTM_INSTALL_DIR`). If Claude Code or Cursor is present on the machine, the agent skill files are auto-installed too.

```bash
export RUNTM_API_KEY=runtm_sk_live_...   # from https://app.runtm.com → Settings → API Keys
runtm-api auth status
```

The skill is embedded in the binary; re-install it at any time via `runtm-api skills install`. The skill itself is small and defers to the docs, which `runtm-api docs [path]` can fetch for agents without a web tool.

### Install a specific version

```bash
curl -fsSL https://runtm.com/install | RUNTM_VERSION=0.1.0 bash
```

### Install via `go install` (Go developers)

If you already have Go 1.23+ and prefer to compile from source:

```bash
go install github.com/runtm-ai/runtm/packages/agent/cmd/runtm-api@latest
runtm-api skills install
```

## Build from source

```bash
make build              # ./bin/runtm-api
make dev                # build + --help against http://localhost:8081
make release            # cross-compile to ./dist (local snapshot)
```

For a full cross-platform release via goreleaser (mirrors what CI runs):

```bash
cd packages/agent && goreleaser release --snapshot --clean
```

Requires Go 1.23+ and `goreleaser` installed locally (only for the snapshot path).

## Commands

| Command | What it does |
|---------|-------------|
| `runtm auth status` | Verify the API key against `/api/v1/me`. |
| `runtm session create` | Create a sandbox (no prompt). |
| `runtm session launch` | Create + fire a prompt as a background task. |
| `runtm session list` | List sessions for the API key. |
| `runtm session status <id>` | Get state + `last_prompt` polling fields. |
| `runtm session prompt <id> <text>` | Stream a prompt as SSE -> JSON lines. |
| `runtm session destroy <id>` | Tear down a sandbox. |
| `runtm session git <id> <op>` | Run a git operation (commit, push, create_branch_and_pr, ...). |
| `runtm template list` | List org templates (requires an org-scoped API key). |
| `runtm template get <id>` | Inspect one template. |
| `runtm deploy list` | List deployments. |
| `runtm deploy get <id>` | Inspect one deployment. |

Run `runtm --help` or `runtm <command> --help` for full flag reference.

## Auth and config

| Setting | Env var | Fallback |
|---------|---------|----------|
| API key | `RUNTM_API_KEY` | `~/.runtm/credentials` (written by `runtm login` in the pip CLI) |
| API URL | `RUNTM_API_URL` | `~/.runtm/config.yaml` then `https://app.runtm.com/api/cloud` |
| Org ID  | `RUNTM_ORG_ID`  | `--org` flag, otherwise auto-resolved from the API key when it's org-scoped |

The org is bound to the API key at creation. `RUNTM_ORG_ID` / `--org` can only
restate that binding — the API returns `403` if they name a different org, or
name any org at all on a personal key. To use org-scoped commands (templates,
team telemetry, team secrets, org instructions, guardrails, skills/MCP), create
an org-scoped key in the dashboard.

API keys are managed in the dashboard at https://app.runtm.com. The same key works for both the pip CLI and this Go CLI.

Production traffic goes through `https://app.runtm.com/api/cloud`, which the
dashboard rewrites to the FastAPI backend's canonical `/api/*` routes. For
local development, point directly at the backend:

```bash
export RUNTM_API_URL=http://localhost:8081/api
```

## Output and exit codes

- Stdout is always JSON. For `session prompt`, stdout is JSON lines (one per SSE event).
- Stderr carries structured error JSON: `{"error": "...", "status": 401, "hint": "..."}`.
- Exit codes: `0` ok, `1` API error, `2` auth error, `3` usage error.

## Skill

One file, [`skills/SKILL.md`](./skills/SKILL.md), teaches AI agents how to use this CLI. It is deliberately short: how to read the docs (`https://docs.runtm.com/llms.txt`, then `<page>.md`, then lift the agent-only `cli:`, `cli-verify:` and `cli-handoff:` lines), the six-step method for building an agent, two golden paths, the rules that fail silently, auth and org context, and the output contract.

Everything else lives in the docs, which are the single source of truth: the Build pages (method), Guides (worked agents and recipes), and API Reference > Patterns (session, template and debugging recipes for the CLI). Change the docs, not the skill, when behaviour changes.

## Layout

```
packages/agent/
  cmd/runtm-api/main.go       # entry point
  internal/auth/              # credential + base URL resolution
  internal/client/            # HTTP wrapper, JSON + SSE
  internal/cmd/               # cobra subcommands
  internal/skills/            # //go:embed wrapper for skills/
  skills/                     # SKILL.md (embedded into binary)
  .goreleaser.yml             # cross-compile + GitHub Releases config
  Makefile                    # local build / dev targets
```

The hosted installer script lives in `runtm-landing/public/install` and is served at `https://runtm.com/install`. It downloads the tarball published by `.github/workflows/release-agent.yml`.

## Roadmap

- Agent-mode auto-detection via env vars (`CLAUDE_CODE`, `CURSOR_AGENT`, etc.) for smarter defaults.
- An MCP server (`runtm mcp serve`) for tools that prefer MCP over CLI invocation.
- Replace the pip CLI entirely with this Go implementation.

## License

Apache-2.0 (matches the parent monorepo).
