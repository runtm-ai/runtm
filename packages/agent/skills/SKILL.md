---
name: runtm
description: "Runtm (Runtime) Cloud CLI for AI agents. Full cloud-API surface: sessions (CRUD + search + files + upload/download + env + deploy + approvals + capability loading + lifecycle + history + events), the agent roster (identity + instructions + evaluation rubric + budget + scorecard + run grades), scheduled agents (cron automation + run-now), org templates (CRUD + build + context + guardrails + owning groups + secrets), guardrail content (allowlist rules, hooks, network rules), skills lifecycle (import, discover, resync, lock), deployments, GitHub App installations, groups, activity telemetry, secrets, instructions, LLM provider keys, external integrations (MCP, tools, Slack, GitHub, Linear, Email). Trigger on: runtm, runtime, runtm cloud, runtime cloud, runtm session, runtime session, cloud sandbox, integration, integrations, mcp, skill, provider key, scheduled agent, cron, automation, agent roster, evals, evaluation, scorecard, guardrail, approvals, deployment, build an agent, create an agent, support agent."
metadata:
  version: "0.11.0"
  repository: https://github.com/runtm-ai/runtm
  tags: runtm,runtime,cli,sandboxes,coding-agents
---

# Runtm (Runtime) Cloud

CLI for [Runtm Cloud](https://app.runtm.com) -- the hosted control plane for cloud sandboxes. When the user says "runtime" or "runtm" they mean this tool. The binary is `runtm-api` (separate from the pip `runtm` CLI which handles local dev).

**This CLI talks to the hosted cloud API only** (through `https://app.runtm.com/api/cloud/...`, which proxies to backend `/api/...`). It covers the same operations the dashboard does, so AI agents can do anything a human does in the UI: create templates, fix broken ones, launch sessions, inspect files, manage secrets, deploy.

Full API reference: https://docs.runtm.com/cloud-api

## Most common path: template → session → run commands

The everyday loop is **create a template, boot a session from it, then connect or exec into it**. Memorize this:

```bash
# 1. Create a template from a repo. --skip-agent does a clone-only build (no AI
#    step -> fast) and implies --build, so the build kicks off immediately.
runtm-api template create \
  --display-name "NuvoOS Dev Environment" \
  --github-repo runtm-ai/landing-page \
  --github-branch main \
  --tier standard \
  --name template \
  --skip-agent
# -> {"id": "28f6e6e6-d73d-4f21-8b1f-312e17e8f47b", "build_status": "pending", ...}

# 2. Wait until the template is ready (only "ready" templates can boot sessions)
runtm-api template get 28f6e6e6-d73d-4f21-8b1f-312e17e8f47b | jq -r .build_status

# 3. Boot a session from the template
runtm-api session create --template-id 28f6e6e6-d73d-4f21-8b1f-312e17e8f47b
# -> {"id": "a6414511-4430-4e1f-8c51-8ea8824dadec", "state": "creating", ...}

# 4a. Attach an interactive shell (raw PTY; requires a TTY on stdin)
runtm-api session connect a6414511-4430-4e1f-8c51-8ea8824dadec

# 4b. Or run one command non-interactively and capture its output + exit code
runtm-api session exec a6414511-4430-4e1f-8c51-8ea8824dadec -- pwd

# 4c. Parsing the output? Use --json for separated streams and no shell noise.
runtm-api session exec a6414511-4430-4e1f-8c51-8ea8824dadec --json -- npm test
# -> {"stdout": "...", "stderr": "...", "exit_code": 0}
```

Use `session connect` when a human wants a live shell; use `session exec` for scripted, one-shot commands. Both need the `sessions:terminal` scope, and both auto-resume a paused sandbox. See the `runtm-sessions` skill for the full recipe.

**Always pass `--json` to `session exec` when you are going to parse the output.** The default is the raw PTY stream, which merges stderr into stdout and carries shell startup noise (mise/nvm banners and the like) that you would otherwise have to filter out with `grep -v`. `--json` captures the two streams separately and strips PTY carriage returns.

To parameterize a template, declare **session arguments** with `--session-arg` (each becomes an env var in the session); supply values at boot with `session create --template-id <uuid> --template-args KEY=VALUE`. See the `runtm-templates` skill.

## Other common path: building an agent

The loop above is for **driving a sandbox yourself**. When the ask is "build me an agent that does X" (support triage, on-call, research, code review), the shape is different and the order matters. Read the **`runtm-build-agent`** skill first.

The one thing to know before you start: **every capability an agent has hangs off one template.**

```
trigger -> roster agent -> template -> { skills, MCP servers, tools, guardrails, context } -> session
```

So a roster agent without `--template` can do nothing special, a skill that is never attached to that template is never loaded, and an attachment made after the last build does nothing until you rebuild. All three fail **silently**. Create the template, attach everything, verify with `template get`, then build **once**. Note that `template create --skip-agent` implies `--build`, so the fast path above will bake an empty template if you meant to attach skills to it.

## Quick Reference

### Sessions

| Task | Command |
|------|---------|
| List sessions | `runtm-api session list` |
| Launch agent + prompt | `runtm-api session launch --prompt "<task>"` |
| Create blank session | `runtm-api session create --agent claude-code` |
| Create session from org template | `runtm-api session create --template-id <uuid>` |
| Boot template session with arg values | `runtm-api session create --template-id <uuid> --template-args KEY=VALUE` |
| Attach interactive terminal (PTY) | `runtm-api session connect <id>` |
| Run one command (scripted) | `runtm-api session exec <id> -- <command>` |
| Run one command, parseable output | `runtm-api session exec <id> --json -- <command>` |
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
| Search sessions with filters | `runtm-api session search -q "<text>" [--agent ...] [--template ...] [--team-mode]` |
| Run's evaluation verdict | `runtm-api session grade <id>` |
| List approval gates | `runtm-api session approvals list <id>` |
| Approve / reject a gate | `runtm-api session approvals resolve <id> <approval_id> --approve\|--reject [--note "..."]` |
| Hot-load skills into a running session | `runtm-api session load-skills <id> <skill_id...>` |
| Hot-load MCP servers | `runtm-api session load-mcps <id> <mcp_id...>` |
| Hot-load tools (by provider slug) | `runtm-api session load-tools <id> <slug...>` |
| Tools loaded in a session | `runtm-api session tools <id>` |
| Files: read/write/list | `runtm-api session file read\|write\|list <id> ...` |
| Files: search/mkdir/rename/delete | `runtm-api session file search\|mkdir\|rename\|delete <id> ...` |
| Files: binary upload/download | `runtm-api session file upload\|download <id> <path> ...` |
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
| Get template detail (incl. attached skills + build staleness) | `runtm-api template get <tmpl_id>` |
| Create new template | `runtm-api template create --display-name "..." --github-repo owner/repo` |
| Create + clone-only build (no AI step) | `runtm-api template create --display-name "..." --github-repo owner/repo --skip-agent` |
| Declare session args (create/update) | `runtm-api template create ... --session-arg KEY=DEFAULT --session-arg '{"key":"ENV","type":"select","options":["dev","prod"]}'` |
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
| Skills/MCP attached to a template | `runtm-api template skills\|mcp <tmpl_id>` (or `skills\|mcp list --template <tmpl_id>`) |
| Attach a skill/MCP to a template | `runtm-api skills\|mcp attach <id> --template <tmpl_id>` |
| Template context (instructions) | `runtm-api template context get\|set\|resolve <tmpl_id>` |
| Template guardrails | `runtm-api template guardrails list\|create\|update\|delete\|resolve <tmpl_id> ...` |
| Owning group / auto-rebuild cron | `runtm-api template update <tmpl_id> --owner-team <team_id> \| --rebuild-schedule '0 6 * * *'` |

#### Verify a template actually loads your skills

The most common template mistake is creating skills and never attaching them, so sessions boot without the behaviour you wrote. `template get` answers this inline — no second call needed:

```bash
runtm-api template get <tmpl_id> | jq '{
  skills: [.skills[].name],
  stale: .attachments_changed_since_build
}'
```

- **`skills: []`** means nothing is attached, however many skills exist in the org. Fix with `runtm-api skills attach <skill_id> --template <tmpl_id>`.
- **`stale: true`** means the attachments changed after the last build, so the snapshot sessions boot from is behind the config. Fix with `runtm-api template build <tmpl_id>`.

Each entry carries `attached_via`: `template` (attached directly), `repo` (via one of the template's repos), or `all` (org-wide). Three commands give the same skills answer, so reach for whichever you thought of first:

```bash
runtm-api template get <tmpl_id> | jq .skills   # inline, plus staleness
runtm-api template skills <tmpl_id>             # template-first
runtm-api skills list --template <tmpl_id>      # skills-first
```

### Agent roster (named agents + the evaluation loop)

Named agents with identity, system instructions, session defaults, an evaluation rubric, and a budget. This is the entity the dashboard's Agents page manages. Omit `--type` for the roster; `--type slack|github|linear|email` manages that platform's trigger integrations instead.

| Task | Command |
|------|---------|
| List roster agents | `runtm-api agents list` |
| Create (headless) | `runtm-api agents create --name X --instructions '...' [--template <slug>]` |
| Edit identity/defaults | `runtm-api agents update <id> --instructions '...' --template <slug> [--clear-template]` |
| Set the evaluation rubric | `runtm-api agents update <id> --evaluator-criteria '{"objective":"...","checks":["..."]}'` |
| Set task values + budget | `runtm-api agents update <id> --economics '{"tasks":{"triage":{"value_usd":10}},"budget":{"monthly_usd_cap":50}}'` |
| Per-agent scorecard | `runtm-api agents scorecard --days 30` |
| One run's verdict | `runtm-api session grade <session_id>` |
| Trigger credential refs | `runtm-api agents trigger-credentials` |
| Delete | `runtm-api agents delete <id> --yes` (delete its triggers first) |
| Linear trigger (headless) | `runtm-api agents create --type linear --linear-api-key lin_api_... --service-user <user_id>` |
| Email trigger (headless) | `runtm-api agents create --type email --name X --agent-id <roster_agent_id>` |

The evaluation loop: set `evaluator_criteria` on the agent, every completed run is graded against it, `session grade` reads one verdict, `agents scorecard` aggregates hit rate, spend, value, and budget. Without a rubric nothing is graded and the scorecard shows zeros.

### Guardrail content (allowlist rules, hooks, network rules)

`guardrails limits|allowlist` manage org settings. The rules themselves are directives that attach to templates, repos, or the whole org, exactly like skills:

| Task | Command |
|------|---------|
| Allowlist rule (allow/ask/deny a command pattern) | `runtm-api guardrails rules create --name X --kind deny --pattern 'git push --force*'` |
| Lifecycle hook (script or prompt on agent events) | `runtm-api guardrails hooks create --name lint-on-stop --event Stop --script './lint.sh'` |
| Network egress rule | `runtm-api guardrails network create --name allow-stripe --kind host --value api.stripe.com` |
| List / attach / detach / lock | `runtm-api guardrails rules\|hooks\|network list\|attach\|detach\|lock ...` |
| Template-scoped guardrails | `runtm-api template guardrails list\|create\|update\|delete\|resolve <tmpl_id>` |
| What a template actually enforces | `runtm-api template guardrails resolve <tmpl_id>` |

### Scheduled agents (cron automation)

Run a prompt on a schedule. Distinct from `runtm-api agents`, which fires on Slack/GitHub *events*; these fire on a *clock*.

| Task | Command |
|------|---------|
| List (with `next_run_at`) | `runtm-api scheduled-agents list` |
| Get one | `runtm-api scheduled-agents get <id>` |
| Create | `runtm-api scheduled-agents create --name X --cron '0 18 * * 1' --prompt "..." [--template <tmpl_id>]` |
| **Run once, right now** | `runtm-api scheduled-agents run-now <id>` |
| Enable / disable | `runtm-api scheduled-agents update <id> --enabled\|--disabled` |
| Change the schedule | `runtm-api scheduled-agents update <id> --cron '0 17 * * 1'` |
| Post results to Slack | `runtm-api scheduled-agents create ... --slack-integration <id> --slack-channel <chan_id>` |
| Delete | `runtm-api scheduled-agents delete <id> --yes` |

**Always `run-now` before enabling.** It executes the same path the cron tick takes — same template resolution, same Slack target, same orchestrator call — so a bad template name or missing integration fails in front of you instead of silently at the scheduled hour. It works on disabled agents, which is what makes this order safe:

```bash
# 1. Create it switched off
runtm-api scheduled-agents create --name weekly-outbound --disabled \
  --cron '0 18 * * 1' --template <tmpl_id> \
  --prompt 'Build this week's outbound lists and post them for approval'

# 2. Prove it works (returns the launched session_id)
runtm-api scheduled-agents run-now <id>
runtm-api session history <session_id>

# 3. Only then turn the schedule on
runtm-api scheduled-agents update <id> --enabled
```

Cron is **5 fields in UTC** — there is no per-agent time zone. 11am Pacific is `0 18 * * *` in winter and `0 17 * * *` under daylight time, so pick the one that matches now and revisit at the DST boundary. `list` reports `next_run_at` (null when disabled) next to `last_run_at` and `last_session_id`, which is the fastest way to check whether a schedule is actually live.

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

### Deployments

The deployments that `session deploy run` ships. Alias: `deploy`.

| Task | Command |
|------|---------|
| List (filter by state) | `runtm-api deployments list [--state ready]` |
| Get one (state, live URL, version) | `runtm-api deployments get <deployment_id>` |
| Stored build/deploy/runtime logs | `runtm-api deployments logs <deployment_id> [--type runtime] [--lines 100] [--search err]` |
| Tear down (URL goes offline) | `runtm-api deployments destroy <deployment_id> --yes` |

After `session deploy run` succeeds, `session get <id> | jq .last_deployment_id` links the session to its deployment.

### GitHub App repo access

Repo access is the precondition for most template work; check it before diagnosing a failed clone or build.

| Task | Command |
|------|---------|
| List installations | `runtm-api github installations` |
| Repos the App can reach | `runtm-api github repos [--installation <uuid>]` |
| Grant access to another repo | `runtm-api github add-repo <installation_uuid> --repo-id N --repo owner/name --oauth-token <tok>` |

### Secrets / Instructions / Guardrails / Providers / Plan

| Area | Commands |
|------|----------|
| Secrets | `runtm-api secrets list\|set\|delete\|resolved [--team]` |
| Instructions | `runtm-api instructions get\|set [--org-scope] [--text "..."\|--clear]` |
| Guardrails | `runtm-api guardrails limits\|allowlist get\|set`, `can-deploy`, `deploy-limits`, `cleanup --yes` |
| Providers (LLM keys) | `runtm-api providers anthropic\|openai get\|set\|delete\|resolved [--org-scope]` |
| Integrations (external) | Skills / MCP servers / tools -- `runtm-api skills\|mcp\|tools create\|get\|list\|update\|delete`; scope a listing with `list --template <tmpl_id>` / `--repo owner/name`; attach with `skills\|mcp attach\|detach\|attachments <id> --template <tmpl_id>` (see `runtm-integrations`) |
| Agent roster (named agents) | `runtm-api agents list\|get\|create\|update\|delete` (no --type), `scorecard`, `trigger-credentials` |
| Trigger integrations (events) | `runtm-api agents ... --type slack\|github\|linear\|email` (see `runtm-agents`) |
| Scheduled agents (cron) | `runtm-api scheduled-agents list\|get\|create\|update\|run-now\|delete` |
| Skills lifecycle | `runtm-api skills import\|discover\|resync\|lock\|unlock\|facets\|upload-file` |
| Groups (owning teams) | `runtm-api groups usage <team_id>`; assign with `--owner-team` on template/skills/mcp update |
| Deployments | `runtm-api deployments list\|get\|logs\|destroy` (alias: `deploy`) |
| GitHub App access | `runtm-api github installations\|repos\|add-repo` |
| Auth | `runtm-api auth status` |

## Endpoint Strategy

Everything hits the **canonical Cloud API** (`/api/...` on `app.runtm.com`). Three deliberate v0 fallbacks remain because they're built specifically for fire-and-forget agent UX:

| Command | Path | Why |
|---------|------|-----|
| `session launch` | `POST /api/v0/sessions/launch` | Documented entry point for webhook / agent workflows; one call creates + prompts. |
| `session status` | `GET /api/v0/sessions/{id}` | v0 returns `last_prompt` polling envelope for fire-and-forget. |
| `session prompt` | `POST /api/v0/sessions/{id}/prompt` | Synchronous SSE stream; canonical equivalent splits into POST 202 + GET events (worse CLI UX). |

Deployments are the one other v0 surface the CLI calls: `runtm-api deployments list|get|logs|destroy` proxies `/api/v0/deployments*` with API-key auth, so the deployment a session ships can be tracked and torn down without switching to the pip CLI.

## Prerequisites

Install the pre-built binary (macOS / Linux × amd64 / arm64, no Go required):

```bash
curl -fsSL https://runtm.com/install | bash
export RUNTM_API_KEY=runtm_sk_live_...   # from https://app.runtm.com > Settings > API Keys
```

The installer drops `runtm-api` in `/usr/local/bin` (override with `RUNTM_INSTALL_DIR=$HOME/.local/bin`) and auto-installs these skill files into `~/.claude/skills/runtm/` and `~/.cursor/skills/runtm/` when those directories exist.

If you already have Go 1.23+ and prefer to compile from source:

```bash
go install github.com/runtm-ai/runtm/packages/agent/cmd/runtm-api@latest
runtm-api skills install
```

### Org context

Org-scoped operations (templates, team telemetry, team secrets, org instructions, guardrails, skills/MCP) need an **org-scoped API key**. Nothing else is required — the org is auto-discovered from the key, so org keys work with no extra setup.

The org is bound to the key when it is created and cannot be overridden at call time:

| Key | What you pass | Result |
|-----|---------------|--------|
| Org-scoped | nothing | Works — org read from the key |
| Org-scoped | `--org` matching the key | Works, redundant |
| Org-scoped | `--org` for another org | `403` |
| Personal | anything | `403` — a personal key can never reach an org |

So `--org` / `RUNTM_ORG_ID` can only restate the key's own binding; they cannot grant access. If an org-scoped command reports the key is personal, the fix is to create an org-scoped key at https://app.runtm.com > Settings > API Keys — not to set the env var.

```bash
runtm-api auth status | jq .organization_id   # null => personal key
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
| 403 | Missing scope, or the key's org doesn't match the request | `runtm-api auth status` to inspect; key may need `templates:write`, `secrets:write`, `guardrails:write`, etc. Also returned when a personal key targets an org, or `--org` names a different org than the key — use an org-scoped key instead. |
| 404 | Wrong ID | Run the matching `list` command first |
| 409 | Conflict (e.g. duplicate name) | Use a different name / --name flag |
| 422 | Body validation failed | Check the canonical endpoint schema at https://docs.runtm.com/cloud-api |
| 429 | Rate limited | Back off (5-10s) and retry |
| 502 on `scheduled-agents run-now` | The run itself failed (bad template name, missing Slack integration) | The `detail` carries the reason — this is run-now working as intended, catching what would otherwise fail silently at the scheduled hour |
| 503 on `scheduled-agents create\|update` | Cloud Scheduler isn't configured in this environment | Create with `--disabled` and drive it with `run-now` |
| 5xx | Sandbox / upstream | Retry once; check https://status.runtm.com |

## Scope Reference

| Operation | Required scope(s) |
|-----------|------------------|
| `session list\|get\|status\|history\|workspace-state\|collaborators` | `sessions:read` |
| `session create\|destroy\|rename\|pause\|resume\|git\|visibility\|heartbeat\|run-server` | `sessions:write` (`sessions:delete` for destroy) |
| `session launch` | `sessions:write` + `sessions:prompt` |
| `session connect\|exec` | `sessions:terminal` |
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
| `providers * get\|resolved` | `integrations:read` (backend scope unchanged) |
| `providers * set\|delete` | `integrations:write` (org needs admin/owner) |
| `scheduled-agents list\|get` | `sessions:read` |
| `scheduled-agents create\|update\|delete\|run-now` | `sessions:write` (admin/owner) |
| `agents list\|get` (roster) | `activity:read` |
| `agents create\|update\|delete` (roster) | `integrations:write` |
| `agents scorecard` / `session grade` | `activity:read` |
| `agents trigger-credentials` | `integrations:read` |
| `session search` | `sessions:read` |
| `session approvals list` | `sessions:read` |
| `session approvals resolve` | `sessions:write` (+ role gate on the approval) |
| `session load-skills\|load-mcps\|load-tools` | `sessions:write` |
| `session tools` / `file download` | `sessions:read` |
| `session file upload` | `sessions:write` |
| `skills import\|resync\|upload-file` | `context:write` |
| `skills discover\|facets` | `context:read` |
| `skills lock\|unlock` / `mcp lock\|unlock` | `context:write` (admin/owner) |
| `guardrails rules\|hooks\|network` reads | `context:read` |
| `guardrails rules\|hooks\|network` writes | `context:write` |
| `template context get\|resolve` | `context:read` |
| `template context set` | `context:write` |
| `template guardrails` reads | `guardrails:read` |
| `template guardrails` writes | `guardrails:write` |
| `groups usage` | `templates:read` |
| `github installations\|repos` | `integrations:read` |
| `github add-repo` | `integrations:write` |
| `deployments list\|get\|logs` | `deployments:read` |
| `deployments destroy` | `deployments:delete` |

## More Skills

- `runtm-sessions` -- session workflow recipes (launch, iterate, deploy, debug).
- `runtm-templates` -- full template lifecycle (create, build, fix, snapshot).
- `runtm-debug` -- inspect a session's state when something is wrong.
- `runtm-integrations` -- add/connect an **external** integration (research API/SDK/CLI/MCP/repos/skills → weigh auth methods → user picks → build definition → connect in the UI); CRUD skills, MCP servers, and tools. NB: "integration" means external tooling; LLM provider keys (Anthropic/OpenAI) live under `runtm-api providers`.
- `runtm-build-agent` -- **"build me an agent that does X"**: the end-to-end assembly recipe across templates, skills/MCPs/tools, guardrails, the roster, and triggers, in the order that avoids baking an empty template.
- `runtm-agents` -- the agent roster (identity, instructions, rubric, budget, scorecard) and its Slack/GitHub/Linear/Email triggers.
- `runtm-automation` -- scheduled agents: cron syntax, the create-disabled → `run-now` → enable order, and debugging a schedule that didn't fire.

## Subcommand Discovery

```bash
runtm-api --help
runtm-api <area> --help            # session, template, agents, scheduled-agents, guardrails, skills, mcp, tools, groups, deployments, github, activity, secrets, instructions, providers, auth
runtm-api session deploy --help    # nested subcommand trees
runtm-api template fix-session --help
```
