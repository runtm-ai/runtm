---
name: runtm-agents
description: "Create, list/find, and edit Runtm Cloud agents: the named agent roster (identity, instructions, evaluation rubric, budget, scorecard) and the Slack/GitHub/Linear/Email trigger integrations that launch coding sessions on events. Use when the user wants to create or configure an agent, set evaluation criteria or budgets, read the agent scorecard or a run's grade, or wire a Slack, GitHub, Linear, or email trigger from the CLI."
metadata:
  version: "0.2.0"
  tags: runtm,runtime,agents,roster,evals,scorecard,slack,github,linear,email,integrations,bots
---

# Runtm Agents

`runtm-api agents` manages two related things. Reference: https://docs.runtm.com/cloud-api/agents/overview.

**The agent roster (no `--type`)**: durable named agents with a description,
system instructions, session defaults, an evaluation rubric, and a budget.

**Trigger integrations (`--type slack|github|linear|email`)**: the platform
bindings that launch a session when an event arrives. Triggers bind to a
roster agent via `config.agent_id`; the roster row is the source of truth for
defaults and editing them fans out to every linked trigger.

| Verb | Roster | Trigger integration |
|------|--------|---------------------|
| Create | `agents create --name X [flags]` | `agents create --type slack\|github\|linear\|email --name X` |
| List | `agents list` | `agents list --type slack\|github\|linear\|email` |
| Get | `agents get <id>` | `agents get <id> --type ...` |
| Edit | `agents update <id> [flags]` | `agents update <id> --type ... [flags]` |
| Delete | `agents delete <id> --yes` | `agents delete <id> --type ... --yes` |

## The roster and the evaluation loop

```bash
# 1. Create the agent with identity and defaults (headless)
runtm-api agents create --name Donna \
  --instructions 'Be terse. Prefer PRs over prose.' \
  --template gtm-machine --agent claude-code

# 2. Give it a rubric so every run gets graded
runtm-api agents update <id> \
  --evaluator-criteria '{"objective":"Ship weekly outbound lists","checks":["lists posted for approval","deduped against contacted"]}'

# 3. Give it a budget and task values so the scorecard can price its work
runtm-api agents update <id> \
  --economics '{"tasks":{"weekly-lists":{"value_usd":30,"minutes":60}},"budget":{"monthly_usd_cap":50}}'

# 4. Read the results
runtm-api session grade <session_id>     # one run's verdict
runtm-api agents scorecard --days 30     # hit rate, spend, value, budget per agent
```

Notes:
- The rubric's `version` bumps automatically on every edit, so grades record
  which rubric produced them.
- `--clear-template` sends an explicit null and clears the default template
  (fanning out to linked triggers).
- Roster deletion is durable only after its triggers are gone; while an
  integration still references the agent, the lazy sync recreates the row.

## Org context + scope

All commands are org-scoped, so they need an **org-scoped API key** — the org is
read from the key, and `--org` / `RUNTM_ORG_ID` cannot substitute for one. Writes
(create/update/delete) need an **admin/owner** key with the `integrations:write`
scope; reads need `integrations:read`.

## Create triggers

Slack, GitHub, and managed Linear finish in a **browser** (you authorize the
app), so `create` returns something to open. Email and manual Linear are
fully headless.

```bash
# Slack — returns an authorize URL to click
runtm-api agents create --type slack --name George
# -> { "authorize_url": "https://slack.com/oauth/v2/authorize?...", "api_app_id": "A..." }
# Open authorize_url, approve it for your workspace; the agent is created on approval.

# GitHub — writes a local page that submits the app manifest to GitHub
runtm-api agents create --type github --name "Runtime Bot"
# -> { "open": "/tmp/runtm-github-app-XXXX.html", "create_url": "..." }
# Open the file in a browser (GitHub requires a form POST, not a plain link); approve the app.

# Linear, managed OAuth: returns an install URL to open
runtm-api agents create --type linear
# Linear, HEADLESS: a manual bot from a Linear personal API key
runtm-api agents create --type linear --linear-api-key lin_api_... --service-user <user_id>

# Email, HEADLESS: provisions an inbox; inbound mail launches sessions
runtm-api agents create --type email --name support-inbox --agent-id <roster_agent_id>
# -> { "inbox_email": "acme-support-inbox@agentmail.to", ... }
```

Prerequisites & notes:

- **Slack** uses the org's stored Slack *app configuration token* (set once in the
  dashboard) to provision the app via `apps.manifest.create`. If it isn't set,
  create returns a 400 — set it in the dashboard first.
- **GitHub** can't be a plain clickable link: the App-manifest flow must be
  POSTed, so the CLI emits an auto-submitting HTML page to open.
- **Linear headless** requires `--service-user`, the Runtm user id the bot's
  sessions run as. The API key is verified against Linear and the webhook is
  registered automatically.
- **Email** availability depends on the platform inbox provider; check
  `GET /api/v1/email/status` if provisioning 400s. `--agent-id` binds the
  inbox to a roster agent so runs inherit its defaults and rubric.
- Optional create flags: `--agent` (default coding agent), `--template`,
  `--github-repo`, `--rate-limit`, `--github-org` / `--return-url` (GitHub).
- Inspect GitHub App repo access afterwards with
  `runtm-api github installations|repos|add-repo`.

## List / find

```bash
runtm-api agents list                         # the roster (no --type)
runtm-api agents list --type slack            # all Slack agents in the org
runtm-api agents list --type linear           # all Linear bots
runtm-api agents list --type email            # all email inboxes
runtm-api agents get <id> --type github       # one trigger's full config
```

Use `list` to discover an agent's `id`, then `get`/`update`/`delete` it.
Email has no get-by-id route; the CLI resolves `get` from the list.

## Edit

`update` is a partial patch — only the flags you pass change; everything else is
left as-is.

```bash
# Change the default template + coding agent
runtm-api agents update <id> --type slack --template my-tmpl --agent codex

# Change the default model and rate limit
runtm-api agents update <id> --type github --model opus --rate-limit 20

# Turn an agent off / on
runtm-api agents update <id> --type slack --disabled
runtm-api agents update <id> --type slack --enabled
```

Top-level flags: `--name`, `--agent` (default_agent), `--template`
(default_template), `--github-repo` (default_github_repo), `--rate-limit`,
`--service-user`, `--enabled` / `--disabled`.

Config flags (the agent's behavior blob, JSON-patch merged server-side):

- `--model` — shorthand for `config.default_model`.
- `--config '<json>'` — merge any other config keys. This is how you set the
  platform-specific options without one flag per key:
  - **Slack**: `triggers` (`new_channel_message`, `new_bot_channel_message`,
    `new_thread_message`, `new_dm_message`), `system_instructions`, `hide_footer`,
    `hide_launch_block`, `channel_template_map`, `event_context_fields`.
  - **GitHub**: `enable_code_review_on_assignment`,
    `enable_review_on_ready_for_review`, `enable_review_on_review_requested`,
    `enable_comment_handling`, `review_filter_label`, `bot_command_prefix`,
    `hide_footer`, `event_context_fields`.

```bash
# Slack: only respond to DMs and thread replies, with a system prompt
runtm-api agents update <id> --type slack --config '{
  "triggers": {"new_dm_message": true, "new_thread_message": true, "new_channel_message": false},
  "system_instructions": "Be concise. Always open a PR."
}'

# GitHub: only review PRs labeled "needs-review", change the mention prefix
runtm-api agents update <id> --type github --config '{
  "review_filter_label": "needs-review",
  "bot_command_prefix": "@runtime"
}'
```

## Delete

```bash
runtm-api agents delete <id> --type slack --yes
```

## Discovery

```bash
runtm-api agents --help
runtm-api agents update --help
```
