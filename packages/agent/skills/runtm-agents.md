---
name: runtm-agents
description: "Create, list/find, and edit Runtm Cloud integration agents — Slack and GitHub bots that launch a coding session when an event arrives (a Slack mention, a GitHub issue/PR). Use when the user wants to set up a Slack/GitHub agent from the CLI, see existing agents, or change an agent's default template, default coding agent, model, repo, rate limit, or triggers."
metadata:
  version: "0.1.0"
  tags: runtm,runtime,agents,slack,github,integrations,bots
---

# Runtm Agents

`runtm-api agents` manages **integration agents** — Slack and GitHub bots that
launch a coding session when something happens (a Slack mention, a GitHub
issue/PR). Reference: https://docs.runtm.com/cloud-api.

| Verb | Command |
|------|---------|
| Create | `runtm-api agents create --type slack\|github --name "<name>"` |
| List | `runtm-api agents list --type slack\|github` |
| Get | `runtm-api agents get <id> --type slack\|github` |
| Edit | `runtm-api agents update <id> --type slack\|github [flags]` |
| Delete | `runtm-api agents delete <id> --type slack\|github --yes` |

`--type` is required on every command (it selects the platform). **Linear is not
supported from the CLI yet** — manage Linear agents in the dashboard.

## Org context + scope

All commands are org-scoped: pass `--org` / `RUNTM_ORG_ID`, or use an org-scoped
key. Writes (create/update/delete) need an **admin/owner** key with the
`integrations:write` scope; reads need `integrations:read`.

## Create

The platform install finishes in a **browser** (you authorize the app), so
`create` returns something to open — it can't complete headlessly.

```bash
# Slack — returns an authorize URL to click
runtm-api agents create --type slack --name George
# -> { "authorize_url": "https://slack.com/oauth/v2/authorize?...", "api_app_id": "A..." }
# Open authorize_url, approve it for your workspace; the agent is created on approval.

# GitHub — writes a local page that submits the app manifest to GitHub
runtm-api agents create --type github --name "Runtime Bot"
# -> { "open": "/tmp/runtm-github-app-XXXX.html", "create_url": "..." }
# Open the file in a browser (GitHub requires a form POST, not a plain link); approve the app.
```

Prerequisites & notes:

- **Slack** uses the org's stored Slack *app configuration token* (set once in the
  dashboard) to provision the app via `apps.manifest.create`. If it isn't set,
  create returns a 400 — set it in the dashboard first.
- **GitHub** can't be a plain clickable link: the App-manifest flow must be
  POSTed, so the CLI emits an auto-submitting HTML page to open.
- Optional create flags: `--agent` (default coding agent), `--template`,
  `--github-repo`, `--rate-limit` (Slack), `--github-org` / `--return-url` (GitHub).

## List / find

```bash
runtm-api agents list --type slack            # all Slack agents in the org
runtm-api agents list --type github | jq '.integrations[] | {id, name, enabled}'
runtm-api agents get <id> --type github       # one agent's full config
```

Use `list` to discover an agent's `id`, then `get`/`update`/`delete` it.

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
