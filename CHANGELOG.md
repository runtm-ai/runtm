# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/runtm-ai/runtm/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/runtm-ai/runtm/releases/tag/v0.1.0
