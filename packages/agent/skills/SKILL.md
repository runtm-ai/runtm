---
name: runtm
description: "Runtm (Runtime) Cloud CLI for AI agents. Full cloud-API surface: sessions (CRUD + files + env + deploy + lifecycle + history + events + visibility + collaborators), org templates (CRUD + build + fix-session + snapshot + secrets), activity telemetry, secrets, instructions, guardrails, integrations. Trigger on: runtm, runtime, runtm cloud, runtime cloud, runtm session, runtime session, cloud sandbox."
metadata:
  version: "0.4.0"
  repository: https://github.com/runtm-ai/runtm
  tags: runtm,runtime,cli,sandboxes,coding-agents
---

# Runtm (Runtime) Cloud

CLI for [Runtm Cloud](https://app.runtm.com) -- the hosted control plane for cloud sandboxes. When the user says "runtime" or "runtm" they mean this tool. The binary is `runtm-api` (separate from the pip `runtm` CLI which handles local dev).

**This CLI talks to the hosted cloud API only** (the routes under `https://app.runtm.com/api/...`). It covers the same operations the dashboard does, so AI agents can do anything a human does in the UI: create templates, fix broken ones, launch sessions, inspect files, manage secrets, deploy.

Full API reference: https://docs.runtm.com/cloud-api

## Quick Reference

### Sessions

| Task | Command |
|------|---------|
| List sessions | `runtm-api session list` |
| Launch agent + prompt | `runtm-api session launch --prompt "<task>"` |
| Create blank session | `runtm-api session create --agent claude-code` |
| Stream prompt | `runtm-api session prompt <id> "<task>"` |
| Stream live event bus | `runtm-api session events <id>` |
| Poll status (last_prompt) | `runtm-api session status <id>` |
| Get canonical detail | `runtm-api session get <id>` |
| Prompt history | `runtm-api session history <id>` |
| Cancel a running prompt | `runtm-api session prompt-cancel <id>` |
| Rewind to a prior prompt | `runtm-api session prompt-rewind <id> --to-index N` |
| Inspect workspace state | `runtm-api session workspace-state <id>` |
| Pause / resume / rename | `runtm-api session pause\|resume\|rename <id> [...]` |
| Bump idle timer | `runtm-api session heartbeat <id>` |
| Change visibility | `runtm-api session visibility <id> private\|team` |
| Per-session instructions | `runtm-api session instructions get\|set <id> ...` |
| Collaborators | `runtm-api session collaborators <id>` |
| Start dev server | `runtm-api session run-server <id> [--port N]` |
| Files: read/write/list | `runtm-api session file read\|write\|list <id> ...` |
| Files: search/mkdir/rename/delete | `runtm-api session file search\|mkdir\|rename\|delete <id> ...` |
| Env vars: get/set/delete | `runtm-api session env get\|set\|delete <id> ...` |
| Env vars: detect / detected | `runtm-api session env detect\|detected <id>` |
| Open PR with changes | `runtm-api session git <id> create_branch_and_pr --pr-title "..."` |
| Generic git ops | `runtm-api session git <id> <op> [flags]` |
| Deploy: info/scaffold/validate/preflight | `runtm-api session deploy info\|scaffold\|validate\|preflight <id>` |
| Deploy: run (SSE) | `runtm-api session deploy run <id>` |
| Destroy session | `runtm-api session destroy <id>` |

### Org templates (full lifecycle)

| Task | Command |
|------|---------|
| List templates | `runtm-api template list` |
| Get template detail | `runtm-api template get <tmpl_id>` |
| Create new template | `runtm-api template create --display-name "..." --github-repo owner/repo` |
| Update metadata | `runtm-api template update <tmpl_id> --display-name "..."` |
| Delete template | `runtm-api template delete <tmpl_id> --yes` |
| Trigger build | `runtm-api template build <tmpl_id>` |
| Stream build logs | `runtm-api template build-logs <tmpl_id>` |
| Past build logs | `runtm-api template build-logs-history <tmpl_id>` |
| Fix a broken template (open session) | `runtm-api template fix-session <tmpl_id>` |
| Save fix-session as new snapshot | `runtm-api template save-snapshot <tmpl_id> --session <session_id>` |
| Discover GitHub repos eligible | `runtm-api template repos` |
| List template secrets | `runtm-api template secrets list <tmpl_id>` |
| Set template secrets | `runtm-api template secrets set <tmpl_id> KEY value [KEY value ...]` |
| Delete a template secret | `runtm-api template secrets delete <tmpl_id> KEY` |

### Activity (telemetry)

| Task | Command |
|------|---------|
| Personal summary | `runtm-api activity summary` |
| Recent prompts | `runtm-api activity recent-prompts --limit 20` |
| Daily breakdown | `runtm-api activity daily --days 7` |
| Per-session usage | `runtm-api activity session-usage <id>` |
| Team summary | `runtm-api activity team-summary` |
| Team activity over time | `runtm-api activity team-activity --days 7` |
| Team members | `runtm-api activity team-members` |

### Secrets / Instructions / Guardrails / Integrations / Plan

| Area | Commands |
|------|----------|
| Secrets | `runtm-api secrets list\|set\|delete\|resolved [--team]` |
| Instructions | `runtm-api instructions get\|set [--org-scope] [--text "..."\|--clear]` |
| Guardrails | `runtm-api guardrails limits\|allowlist get\|set`, `can-deploy`, `deploy-limits`, `cleanup --yes` |
| Integrations | `runtm-api integrations anthropic\|openai get\|set\|delete\|resolved [--org-scope]` |
| Auth | `runtm-api auth status` |

## Endpoint Strategy

Everything hits the **canonical Cloud API** (`/api/...` on `app.runtm.com`). Three deliberate v0 fallbacks remain because they're built specifically for fire-and-forget agent UX:

| Command | Path | Why |
|---------|------|-----|
| `session launch` | `POST /api/v0/sessions/launch` | Documented entry point for webhook / agent workflows; one call creates + prompts. |
| `session status` | `GET /api/v0/sessions/{id}` | v0 returns `last_prompt` polling envelope for fire-and-forget. |
| `session prompt` | `POST /api/v0/sessions/{id}/prompt` | Synchronous SSE stream; canonical equivalent splits into POST 202 + GET events (worse CLI UX). |

The CLI **does not call OSS-only routes** like `/api/v0/deployments` (that's the pip CLI's territory).

## Prerequisites

Requires Go 1.23+ on PATH (`brew install go` / https://go.dev/dl/).

```bash
go install github.com/runtm-ai/runtm/packages/agent/cmd/runtm-api@latest
runtm-api skills install    # auto-detects Claude Code / Cursor and installs SKILL.md files

export RUNTM_API_KEY=runtm_sk_live_...   # from https://app.runtm.com > Settings > API Keys
```

Org context for org-scoped operations (templates, team telemetry, team secrets, org instructions, guardrails):

```bash
export RUNTM_ORG_ID=org_abc123   # or pass --org org_abc123 per command
```

## Required Input Resolution

1. Check context first (prior output, conversation, env vars).
2. If a value is missing, run the matching `list` / `get` first:
   - Session ID: `runtm-api session list`
   - Template ID: `runtm-api template list`
3. If still ambiguous, ask the user.
4. Never run a command with an unresolved placeholder like `<id>` or `<org>`.

## Auth

```bash
runtm-api auth status   # returns authenticated, scopes, org, tenant
```

If `authenticated: false`, ask the user to set `RUNTM_API_KEY` (or run `runtm-api login` in the pip CLI). API keys are managed at https://app.runtm.com > Settings > API Keys.

## Output

- All commands emit JSON to stdout. Parse it directly.
- Errors go to stderr as JSON: `{"error": "...", "status": 401, "hint": "..."}`.
- Exit codes: `0` success, `1` API error, `2` auth error, `3` usage error.
- SSE commands (`session prompt`, `session events`, `session deploy run`, `template build-logs`) stream JSON lines: `{"event": "<type>", "data": <payload>}`. Stream ends with `event: "done"`.

## Error Recovery

| Status | Cause | Fix |
|--------|-------|-----|
| 401 | API key invalid or missing | `RUNTM_API_KEY` or rotate in dashboard |
| 403 | Missing scope or wrong org | `runtm-api auth status` to inspect; key may need `templates:write`, `secrets:write`, `guardrails:write`, etc. |
| 404 | Wrong ID | Run the matching `list` command first |
| 409 | Conflict (e.g. duplicate name) | Use a different name / --name flag |
| 422 | Body validation failed | Check the canonical endpoint schema at https://docs.runtm.com/cloud-api |
| 429 | Rate limited | Back off (5-10s) and retry |
| 5xx | Sandbox / upstream | Retry once; check https://status.runtm.com |

## Scope Reference

| Operation | Required scope(s) |
|-----------|------------------|
| `session list\|get\|status\|history\|workspace-state\|collaborators` | `sessions:read` |
| `session create\|destroy\|rename\|pause\|resume\|git\|visibility\|heartbeat\|run-server` | `sessions:write` (`sessions:delete` for destroy) |
| `session launch` | `sessions:write` + `sessions:prompt` |
| `session prompt\|prompt-cancel\|events` | `sessions:prompt` |
| `session prompt-rewind` | `sessions:write` |
| `session file read\|list\|search` | `sessions:read` |
| `session file write\|delete\|rename\|mkdir` | `sessions:write` |
| `session env get\|detected` | `secrets:read` |
| `session env set\|delete\|detect` | `secrets:write` |
| `session instructions get` | `context:read` |
| `session instructions set` | `context:write` |
| `session deploy info` | `deployments:read` |
| `session deploy scaffold\|validate\|preflight\|run` | `deployments:write` |
| `template list\|get\|repos\|build-logs\|build-logs-history` | `templates:read` |
| `template create\|update\|fix-session\|save-snapshot` | `templates:write` |
| `template build` | `templates:build` |
| `template delete` | `templates:delete` |
| `template secrets list` | `secrets:read` |
| `template secrets set\|delete` | `secrets:write` |
| `activity *` | `activity:read` |
| `secrets list\|resolved` | `secrets:read` |
| `secrets set\|delete` | `secrets:write` (team needs admin/owner role) |
| `instructions get` | `context:read` |
| `instructions set` | `context:write` (org needs admin/owner role) |
| `guardrails limits\|allowlist get` | `guardrails:read` |
| `guardrails limits\|allowlist set` | `guardrails:write` (admin/owner) |
| `guardrails can-deploy\|deploy-limits` | `deployments:read` |
| `guardrails cleanup` | `guardrails:write` (admin/owner) |
| `integrations * get\|resolved` | `integrations:read` |
| `integrations * set\|delete` | `integrations:write` (org needs admin/owner) |

## More Skills

- `runtm-sessions` -- session workflow recipes (launch, iterate, deploy, debug).
- `runtm-templates` -- full template lifecycle (create, build, fix, snapshot).
- `runtm-debug` -- inspect a session's state when something is wrong.

## Subcommand Discovery

```bash
runtm-api --help
runtm-api <area> --help            # session, template, activity, secrets, instructions, guardrails, integrations, auth
runtm-api session deploy --help    # nested subcommand trees
runtm-api template fix-session --help
```
