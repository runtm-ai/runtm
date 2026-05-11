# runtm (agent CLI)

A small Go CLI plus AI agent skills that let coding agents (Claude Code, Cursor, Codex, etc.) drive Runtm Cloud programmatically. Install it, set an API key, and tell your local agent:

> _"Use runtm to launch a session against our Internal API template, fix the auth middleware, and open a PR."_

The agent reads the bundled `SKILL.md`, knows which `runtm` commands to run, and executes them autonomously.

This package is intentionally separate from the pip `runtm` CLI under [`packages/cli/`](../cli/). The pip CLI is a developer-facing tool with interactive prompts, local sandboxes, and deploy workflows. The Go CLI in this directory is a **thin HTTP wrapper** designed for AI agents: stdlib JSON in, stdlib JSON out, predictable exit codes, no interactive UI.

## Install

Requires Go 1.23+ on the customer's machine (`brew install go` / `https://go.dev/dl/`).

One-liner via the install script (also drops skills into `~/.claude` / `~/.cursor`):

```bash
curl -fsSL https://runtm.com/install | sh
export RUNTM_API_KEY=runtm_sk_live_...   # from https://app.runtm.com Settings > API Keys
runtm auth status
```

Or install the binary directly:

```bash
go install github.com/runtm-ai/runtm/packages/agent/cmd/runtm@latest
```

The installer is intentionally `go install`-based: no GitHub release workflow, no per-platform binaries to host, just whatever Go produces from the module. The repo must be public (it is).

## Build from source

```bash
make build              # ./bin/runtm
make dev                # build + --help against http://localhost:8081
make release            # cross-compile to ./dist
```

Requires Go 1.23+.

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
| `runtm template list` | List org templates (requires `--org` / `RUNTM_ORG_ID`). |
| `runtm template get <id>` | Inspect one template. |
| `runtm deploy list` | List deployments. |
| `runtm deploy get <id>` | Inspect one deployment. |

Run `runtm --help` or `runtm <command> --help` for full flag reference.

## Auth and config

| Setting | Env var | Fallback |
|---------|---------|----------|
| API key | `RUNTM_API_KEY` | `~/.runtm/credentials` (written by `runtm login` in the pip CLI) |
| API URL | `RUNTM_API_URL` | `~/.runtm/config.yaml` then `https://app.runtm.com/api` |
| Org ID  | `RUNTM_ORG_ID`  | `--org` flag |

API keys are managed in the dashboard at https://app.runtm.com. The same key works for both the pip CLI and this Go CLI.

## Output and exit codes

- Stdout is always JSON. For `session prompt`, stdout is JSON lines (one per SSE event).
- Stderr carries structured error JSON: `{"error": "...", "status": 401, "hint": "..."}`.
- Exit codes: `0` ok, `1` API error, `2` auth error, `3` usage error.

## Skills

Three files in [`skills/`](./skills) teach AI agents how to use this CLI:

- [`SKILL.md`](./skills/SKILL.md) - command cheat sheet, auth, error recovery, trigger words (`runtm`, `runtime`).
- [`runtm-sessions.md`](./skills/runtm-sessions.md) - multi-step workflows (launch from template, iterate, poll, open PR).
- [`runtm-templates.md`](./skills/runtm-templates.md) - discovering and inspecting org templates.

These are workflow recipes, not API documentation. Full endpoint details live at https://docs.runtm.com/cloud-api.

## Layout

```
packages/agent/
  cmd/runtm/main.go           # entry point
  internal/auth/              # credential + base URL resolution
  internal/client/            # HTTP wrapper, JSON + SSE
  internal/cmd/               # cobra subcommands
  skills/                     # SKILL.md files for AI agents
  install.sh                  # one-liner installer
  Makefile                    # build / dev / release
```

## Roadmap

- `runtm skills install` - auto-install skills for the detected AI tool.
- Agent-mode auto-detection via env vars (`CLAUDE_CODE`, `CURSOR_AGENT`, etc.) for smarter defaults.
- An MCP server (`runtm mcp serve`) for tools that prefer MCP over CLI invocation.
- Replace the pip CLI entirely with this Go implementation.

## License

Apache-2.0 (matches the parent monorepo).
