---
name: runtm-sessions
description: "Multi-step workflow recipes for Runtm Cloud sessions: launching agents, iterating with prompts, polling status, reading/writing files, managing env vars, and opening PRs."
metadata:
  version: "0.2.0"
  tags: runtm,runtime,sessions,workflows,sandboxes
---

# Runtm Sessions

Workflow recipes for the `runtm session` subcommands. For endpoint details see https://docs.runtm.com/cloud-api/sessions.

## When to use which command

| Goal | Use |
|------|-----|
| Fire-and-forget background task | `session launch` (v0 -- creates + prompts in one call) |
| Interactive, streaming response right now | `session create` then `session prompt` |
| Iterate on the same session across prompts | `session prompt` repeatedly with the same `<id>` |
| Just check status (polling) | `session status <id>` (v0 polling shape with last_prompt) |
| Inspect full session metadata | `session get <id>` (canonical) |
| Hold a sandbox without spending | `session pause` then `session resume` later |
| Edit files programmatically | `session file write` |
| Inject configuration | `session env set <id> KEY=VAL ...` |

## Recipe: launch an agent from scratch

```bash
# Fire-and-forget
runtm session launch \
  --prompt "Build a REST API with FastAPI that manages TODO items" \
  --agent claude-code \
  --on-complete pause \
  --ttl-minutes 60
# Returns: {"id": "86e11104-...", "state": "creating", ...}

# Poll until done (last_prompt.status -> completed | error | timed_out)
runtm session status 86e11104-...

# Open a PR with the agent's changes
runtm session git 86e11104-... create_branch_and_pr \
  --pr-title "Add TODO REST API" \
  --pr-body "Implements CRUD endpoints."
```

## Recipe: interactive iteration

```bash
# 1. Create a blank session
runtm session create --agent claude-code --on-complete keep_alive
# Returns: {"id": "...", "state": "creating", ...}

# 2. Wait for it to be running
runtm session get <id>   # poll until .state == "running"

# 3. First prompt (streams SSE as JSON lines)
runtm session prompt <id> "Build a REST API for managing invoices"

# 4. Follow-up
runtm session prompt <id> "Add pagination and filtering to the list endpoint"

# 5. Open PR
runtm session git <id> create_branch_and_pr \
  --pr-title "Invoice API with pagination"

# 6. Cleanup
runtm session destroy <id>
```

## Recipe: pre-seed files and env, then prompt

```bash
runtm session create --agent claude-code --on-complete keep_alive
# Wait for running...

# Write a config file
runtm session file write <id> /home/user/.env --content "API_KEY=abc123\nDEBUG=1"

# Set runtime env vars
runtm session env set <id> NODE_ENV=development DATABASE_URL=postgres://...

# Verify
runtm session file list <id> --path /home/user
runtm session env get <id>   # values come back masked

# Prompt the agent against the prepared workspace
runtm session prompt <id> "Use the config in .env to wire the DB connection."
```

## Recipe: pause and resume

```bash
# Long task done, but might revisit
runtm session pause <id>   # sandbox is frozen, no compute cost

# Later
runtm session resume <id>  # back to running state
runtm session prompt <id> "Continue from where we left off."
```

## Polling pattern

`session status` returns a v0 envelope tuned for polling:

```json
{
  "state": "running",
  "last_prompt": {
    "status": "running",
    "started_at": "...",
    "prompt_preview": "Fix the auth..."
  },
  "lifecycle": {
    "on_complete": "pause",
    "ttl_minutes": 60,
    "ttl_expires_at": "..."
  }
}
```

Stop polling when `last_prompt.status` is one of `completed`, `error`, `timed_out`. The `summary` field holds the agent's final response (truncated to 500 chars).

For high-volume workflows prefer webhooks: https://docs.runtm.com/guides/webhooks-and-triggers.

## Reading SSE prompt output

`session prompt` streams JSON lines:

```
{"event":"assistant_message","data":{"type":"assistant_message","content":"I'll add..."}}
{"event":"tool_use","data":{"type":"tool_use","name":"Edit","input":{...}}}
{"event":"tool_result","data":{...}}
{"event":"result","data":{"content":"Done","metadata":{"cost_usd":0.018}}}
{"event":"done","data":{"message":"Stream complete"}}
```

Read until you see an event of type `done` or `error`. Pipe through `jq` to filter:

```bash
runtm session prompt <id> "..." | jq -c 'select(.event == "result")'
```

## Lifecycle policies (`--on-complete`)

| Value | Behavior |
|-------|----------|
| `pause` (default) | Sandbox pauses; resumable. Cheap to keep around. |
| `destroy` | Sandbox torn down immediately. Use for one-shot tasks. |
| `keep_alive` | Sandbox stays running (until TTL). Use when iterating. |

`--ttl-minutes` is the hard upper bound (max 1440). Safety net for background agents.

## Git operations

| Operation | Purpose |
|-----------|---------|
| `status` | Inspect repo state, current branch, dirty files |
| `commit` | Commit changes (requires `--message`) |
| `push` | Push current branch |
| `create_branch_and_pr` | Branch + commit + push + open PR in one call (typical end-of-task) |
| `list_branches` | List existing branches |
| `init_repo` | Initialize a fresh repo |

Working directory defaults to `/home/user`. Set `--working-dir` if the repo is elsewhere (e.g. monorepos with multiple workdirs).

## File operations

```bash
# Read a single file
runtm session file read <id> /home/user/main.py

# Write (overwrites)
runtm session file write <id> /home/user/main.py --content "$(cat local.py)"

# List a directory
runtm session file list <id> --path /home/user
```

Files require `sessions:read` (list / read) or `sessions:write` (write). For binary or large files prefer the upload/download endpoints documented at https://docs.runtm.com/cloud-api/sessions (not yet exposed by this CLI).

## Env vars

```bash
# Inspect (values are masked: "*****")
runtm session env get <id>

# Set multiple at once
runtm session env set <id> NODE_ENV=development DATABASE_URL=postgres://...

# Remove one
runtm session env delete <id> DATABASE_URL
```

Env-var endpoints use the `secrets:read` and `secrets:write` scopes -- not `sessions:write`. Confirm the API key has those scopes via `runtm auth status` before suggesting env changes to the user.
