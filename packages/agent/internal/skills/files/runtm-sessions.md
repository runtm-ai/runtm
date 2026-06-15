---
name: runtm-sessions
description: "Multi-step workflow recipes for Runtm Cloud sessions: launching agents, iterating with prompts, polling status, reading/writing files, managing env vars, and opening PRs."
metadata:
  version: "0.4.0"
  tags: runtm,runtime,sessions,workflows,sandboxes
---

# Runtm Sessions

Workflow recipes for the `runtm-api session` subcommands. For endpoint details see https://docs.runtm.com/cloud-api/sessions.

## When to use which command

| Goal | Use |
|------|-----|
| Boot a session from a prebuilt org template | `session create --template-id <uuid>` |
| Run a single shell command in a session | `session exec <id> -- <command>` (scripted, returns its exit code) |
| Open a live interactive shell | `session connect <id>` (raw PTY; needs a TTY) |
| Fire-and-forget background task | `session launch` (v0 -- creates + prompts in one call) |
| Interactive, streaming response right now | `session create` then `session prompt` |
| Iterate on the same session across prompts | `session prompt` repeatedly with the same `<id>` |
| Just check status (polling) | `session status <id>` (v0 polling shape with last_prompt) |
| Inspect full session metadata | `session get <id>` (canonical) |
| Hold a sandbox without spending | `session pause` then `session resume` later |
| Edit files programmatically | `session file write` |
| Inject configuration | `session env set <id> KEY=VAL ...` |

## Recipe: boot a session from a template, then run commands

This is the most common path -- create a template once, then spin up sessions from it and drive them with `connect` / `exec`.

```bash
# 1. Create a template (clone-only build; --skip-agent implies --build)
runtm-api template create \
  --display-name "NuvoOS Dev Environment" \
  --github-repo runtm-ai/landing-page \
  --github-branch main \
  --tier standard \
  --name template \
  --skip-agent
# -> {"id": "28f6e6e6-d73d-4f21-8b1f-312e17e8f47b", "build_status": "pending", ...}

# 2. Wait until it is ready (only "ready" templates boot)
runtm-api template get 28f6e6e6-d73d-4f21-8b1f-312e17e8f47b | jq -r .build_status

# 3. Create a session from the template
runtm-api session create --template-id 28f6e6e6-d73d-4f21-8b1f-312e17e8f47b
# -> {"id": "a6414511-4430-4e1f-8c51-8ea8824dadec", "state": "creating", ...}
# If the template declares session args, supply values with --template-args
# (repeatable / comma-separated), e.g. --template-args BRANCH=dev,ENV=staging.
# See the runtm-templates skill for declaring args with --session-arg.

# 4. Run a command non-interactively (waits for it to finish, prints output)
runtm-api session exec a6414511-4430-4e1f-8c51-8ea8824dadec -- pwd

# 5. Or attach a live interactive shell
runtm-api session connect a6414511-4430-4e1f-8c51-8ea8824dadec
```

## Recipe: run commands in a session (connect vs exec)

Two ways to get a shell against a session's sandbox, both over the same terminal WebSocket the dashboard uses (scope: `sessions:terminal`):

```bash
# Non-interactive: run one command, stream its output, exit with its exit code.
# A throwaway PTY is used, so it never disturbs interactive terminals. Put the
# command after `--` so runtm-api does not parse its flags.
runtm-api session exec <id> -- pwd
runtm-api session exec <id> -- ls -la /workspace
runtm-api session exec <id> -- "npm test"
runtm-api session exec <id> --timeout 120 -- ./long-build.sh   # abort after N seconds

# Interactive: attach a raw PTY. Keystrokes (incl. Ctrl-C) pass through; window
# resizes follow. Requires a TTY on stdin. Exit the remote shell to disconnect.
runtm-api session connect <id>
runtm-api session connect <id> --terminal default   # share the dashboard terminal
```

Use `exec` for automation and scripted checks; use `connect` only when a human is at a real terminal. `exec` output is the raw PTY stream and may contain minor terminal formatting.

## Recipe: launch an agent from scratch

```bash
# Fire-and-forget
runtm-api session launch \
  --prompt "Build a REST API with FastAPI that manages TODO items" \
  --agent claude-code \
  --on-complete pause \
  --ttl-minutes 60
# Returns: {"id": "86e11104-...", "state": "creating", ...}

# Poll until done (last_prompt.status -> completed | error | timed_out)
runtm-api session status 86e11104-...

# Open a PR with the agent's changes
runtm-api session git 86e11104-... create_branch_and_pr \
  --pr-title "Add TODO REST API" \
  --pr-body "Implements CRUD endpoints."
```

## Recipe: interactive iteration

```bash
# 1. Create a blank session
runtm-api session create --agent claude-code --on-complete keep_alive
# Returns: {"id": "...", "state": "creating", ...}

# 2. Wait for it to be running
runtm-api session get <id>   # poll until .state == "running"

# 3. First prompt (streams SSE as JSON lines)
runtm-api session prompt <id> "Build a REST API for managing invoices"

# 4. Follow-up
runtm-api session prompt <id> "Add pagination and filtering to the list endpoint"

# 5. Open PR
runtm-api session git <id> create_branch_and_pr \
  --pr-title "Invoice API with pagination"

# 6. Cleanup
runtm-api session destroy <id>
```

## Recipe: pre-seed files and env, then prompt

```bash
runtm-api session create --agent claude-code --on-complete keep_alive
# Wait for running...

# Write a config file
runtm-api session file write <id> /home/user/.env --content "API_KEY=abc123\nDEBUG=1"

# Set runtime env vars
runtm-api session env set <id> NODE_ENV=development DATABASE_URL=postgres://...

# Verify
runtm-api session file list <id> --path /home/user
runtm-api session env get <id>   # values come back masked

# Prompt the agent against the prepared workspace
runtm-api session prompt <id> "Use the config in .env to wire the DB connection."
```

## Recipe: pause and resume

```bash
# Long task done, but might revisit
runtm-api session pause <id>   # sandbox is frozen, no compute cost

# Later
runtm-api session resume <id>  # back to running state
runtm-api session prompt <id> "Continue from where we left off."
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
runtm-api session prompt <id> "..." | jq -c 'select(.event == "result")'
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
runtm-api session file read <id> /home/user/main.py

# Write (overwrites)
runtm-api session file write <id> /home/user/main.py --content "$(cat local.py)"

# List a directory
runtm-api session file list <id> --path /home/user
```

Files require `sessions:read` (list / read) or `sessions:write` (write). For binary or large files prefer the upload/download endpoints documented at https://docs.runtm.com/cloud-api/sessions (not yet exposed by this CLI).

## Env vars

```bash
# Inspect (values are masked: "*****")
runtm-api session env get <id>

# Set multiple at once
runtm-api session env set <id> NODE_ENV=development DATABASE_URL=postgres://...

# Remove one
runtm-api session env delete <id> DATABASE_URL
```

Env-var endpoints use the `secrets:read` and `secrets:write` scopes -- not `sessions:write`. Confirm the API key has those scopes via `runtm-api auth status` before suggesting env changes to the user.
