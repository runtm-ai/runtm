---
name: runtm-templates
description: "Full lifecycle workflows for Runtm Cloud org templates: discover, create, build, monitor builds, fix broken templates via fix-session, save snapshots, manage template secrets."
metadata:
  version: "0.4.0"
  tags: runtm,runtime,templates,org,workflows
---

# Runtm Templates

Workflow recipes for `runtm-api template`. For endpoint reference: https://docs.runtm.com/cloud-api/templates.

## What is an org template

Org templates are **snapshots of fully configured sandbox environments**. Each template can bake in:

- One or many Git repos cloned to their workdirs
- Language runtimes and dependencies pre-installed
- Multiple services (web, api, database, cache, workers)
- Required secrets
- Default agent system instructions
- Compute tier (basic / standard / max)
- One or more coding agents the snapshot was built for

A session created from a template boots instantly with the environment ready -- no install commands, no waiting on dependency builds.

## All template commands require org context

```bash
export RUNTM_ORG_ID=org_abc123    # or pass --org org_abc123 each time
```

Without org context, every `runtm-api template` command surfaces a clear error.

## Recipe: discover what exists

```bash
runtm-api template list                 # all templates
runtm-api template get <tmpl_id>        # full config for one
runtm-api template repos                # GitHub repos eligible for new templates
```

Key fields to inspect:

- `build_status`: `pending` | `building` | `ready` | `failed`. Only `ready` templates can boot sessions.
- `has_all_required`: false means a template will fail to boot until missing secrets are set.
- `services`: array of detected services (web, api, db). Each has its own port + start_cmd.
- `agents`: list of coding agents the snapshot was built for.

## Recipe: create a new template

```bash
# 1. Verify the repo is accessible
runtm-api template repos | jq '.repos[] | select(.full_name == "acme/my-app")'

# 2. Create the template record (build_status starts as 'pending')
runtm-api template create \
  --display-name "Internal API" \
  --name "internal-api" \
  --github-repo "acme/my-app" \
  --github-branch "main" \
  --tier "basic"
# Returns: {"id": "tmpl_abc...", "build_status": "pending", ...}

# 3. Trigger the build
runtm-api template build tmpl_abc...
# Returns 202 (build runs as background job)

# 4. Stream the build progress
runtm-api template build-logs tmpl_abc...
# Streams JSON lines until event "done"

# 5. Confirm completion
runtm-api template get tmpl_abc... | jq '{build_status, has_all_required}'
```

### Faster: create + clone-only build in one call (`--skip-agent`)

`--skip-agent` on `template create` runs a clone-only build with no AI step, and **implies `--build`** -- so steps 2 and 3 above collapse into a single command. Best when the repo just needs cloning and you'll finish setup inside a session.

```bash
runtm-api template create \
  --display-name "NuvoOS Dev Environment" \
  --github-repo runtm-ai/landing-page \
  --github-branch main \
  --tier standard \
  --name template \
  --skip-agent
# -> {"id": "28f6e6e6-d73d-4f21-8b1f-312e17e8f47b", "build_status": "pending", ...}

# Poll until ready, then boot a session from it:
runtm-api template get 28f6e6e6-... | jq -r .build_status   # wait for "ready"
runtm-api session create --template-id 28f6e6e6-...
# Then drive the session with `session exec <id> -- <cmd>` or `session connect <id>`
# (see the runtm-sessions skill).
```

`--display-name` and `--github-repo` are required. `--name` sets the slug (auto-derived from the repo if omitted); `--tier` is one of basic / standard / max. Passing `--skip-agent` to the standalone `template build` command skips the AI step on a rebuild the same way.

## Recipe: declare session arguments

Session arguments are values collected when a member launches a session from the template; each is injected into the sandbox as an **environment variable**. Declare them on `template create` or `template update` with the repeatable `--session-arg` flag.

Two forms per `--session-arg`:

| Form | Meaning |
|------|---------|
| `KEY=DEFAULT` | Optional text arg, defaulting to `DEFAULT` |
| `KEY` (no `=`) | **Required** text arg, no default |
| `'{"key":...}'` (JSON) | Full control: type (`text`/`select`/`boolean`), `options`, `default`, `required`, `label`, `help_text` |

A `select` arg requires a non-empty `options` array. `label` defaults to the key.

```bash
# Create a template that declares three session args
runtm-api template create --display-name 'Rich Args Demo' \
  --github-repo runtm-ai/landing-page --tier standard \
  --session-arg BRANCH=main \
  --session-arg '{"key":"ENV","type":"select","options":["dev","staging","prod"],"default":"dev","required":true,"label":"Environment","help_text":"Target deploy env"}' \
  --session-arg '{"key":"VERBOSE","type":"boolean","default":"false","label":"Verbose logging"}' \
  --skip-agent
# BRANCH -> optional text (default "main"); ENV -> required dropdown; VERBOSE -> checkbox.
```

On `create`, session args are applied via a follow-up `PATCH` (the create endpoint doesn't accept them directly), so the create works with or without `--build` / `--skip-agent`.

On `update`, `--session-arg` **replaces the entire set** (it's not additive). Pass every arg you want to keep:

```bash
# Replace the template's session args with a single BRANCH arg
runtm-api template update 9ff0e46a-ff1f-4440-b2ee-410b07984584 --session-arg BRANCH=main
```

### Supplying values at launch

When booting a session from a template, pass values for its declared args with `--template-args KEY=VALUE` (repeatable / comma-separated; only valid with `--template-id`). Omitted optional args fall back to their default; missing required args are rejected.

```bash
runtm-api session create \
  --template-id 9ff0e46a-ff1f-4440-b2ee-410b07984584 \
  --template-args BRANCH=dev \
  --agent claude-code \
  --mode interactive
# The sandbox boots with BRANCH=dev (plus ENV/VERBOSE defaults) in its environment.
```

## Recipe: fix a broken template

When a template build fails OR a session born from the template can't run because dependencies drifted, **fix-session** is the recovery path:

```bash
# 1. Open a fix-session that boots the template's sandbox
runtm-api template fix-session tmpl_abc...
# Returns: {"session_id": "ses_xyz", "template_id": "tmpl_abc..."}

# 2. Prompt the agent to diagnose + fix inside the fix-session
runtm-api session prompt ses_xyz "The build is failing because of a missing dependency. Run npm install, fix the package.json, and verify the dev server starts on port 3000."

# 3. Inspect what changed
runtm-api session file list ses_xyz --path /home/user/project
runtm-api session workspace-state ses_xyz | jq '.session.dirty_files'

# 4. Once the fix-session works end-to-end, promote it to the template snapshot
runtm-api template save-snapshot tmpl_abc... --session ses_xyz
# Existing sessions keep using the old snapshot; new ones use the new one.

# 5. Clean up the fix-session (optional -- it auto-pauses anyway)
runtm-api session destroy ses_xyz
```

This is the agent's path to **fix template issues from the terminal sandbox** -- equivalent to what an admin does manually in the dashboard.

## Recipe: monitor build state

```bash
# Stream live build logs for an in-progress build
runtm-api template build-logs tmpl_abc...

# Or fetch the persisted log history (after build completed)
runtm-api template build-logs-history tmpl_abc... | jq '.logs[0].content'
```

## Recipe: rebuild after changes

```bash
# Re-trigger build after the underlying repo changed (e.g. new dependency)
runtm-api template build tmpl_abc...
runtm-api template build-logs tmpl_abc...
```

The fast-rebuild path (no full reinstall) kicks in automatically when only instructions or skill files changed; otherwise the full agent build runs.

## Template secrets

Templates declare required env-var names without storing values. Secrets resolve at session boot from the org's team secrets.

```bash
# Inspect what's required + configured
runtm-api template secrets list tmpl_abc...
# {"required_secrets": ["DATABASE_URL"], "secrets": [...], "has_all_required": true}

# Set values (encrypted at rest)
runtm-api template secrets set tmpl_abc... DATABASE_URL "postgres://..." STRIPE_KEY "sk_..."

# Remove a specific secret
runtm-api template secrets delete tmpl_abc... STRIPE_KEY
```

## Recipe: attach skills & MCP servers

Templates can carry **skills** and **MCP servers**, so every session launched
from the template loads them automatically — the same "Session context" wiring
the dashboard offers on a template. Skills/MCP servers are created with the
`skills`/`mcp` commands (see the `runtm-directives` skill); attaching is what
binds them to a template.

```bash
# List what a template currently loads
runtm-api template skills tmpl_abc...   # attached skills
runtm-api template mcp tmpl_abc...      # attached MCP servers

# Attach an existing skill / MCP server to the template
runtm-api skills attach <skill_id> --template tmpl_abc...
runtm-api mcp attach <mcp_id> --template tmpl_abc...

# Detach (leaves the directive's other attachments untouched)
runtm-api skills detach <skill_id> --template tmpl_abc...
```

`attach` merges with the directive's existing scope (repos / other templates /
all-repos), so attaching to a template never disturbs unrelated attachments.
Full authoring + attachment workflow lives in the `runtm-directives` skill.
Requires `context:write` on the key.

## Recipe: clean up

```bash
# Delete must be confirmed
runtm-api template delete tmpl_abc...        # blocks, shows hint
runtm-api template delete tmpl_abc... --yes  # actually deletes
```

Deletion does not affect already-running sessions that were created from the template -- those keep using their own snapshot until destroyed.

## Permissions

| Action | Required role | Required scope |
|--------|--------------|---------------|
| List / get / build-logs | Member | `templates:read` |
| Create / update / fix-session / save-snapshot | Admin or Owner | `templates:write` |
| Build | Admin or Owner | `templates:build` |
| Delete | Admin or Owner | `templates:delete` |
| Manage template secrets | Admin or Owner | `secrets:write` |
| List attached skills / MCP servers | Member | `context:read` |
| Attach / detach skills / MCP servers | Admin or Owner | `context:write` |

If an operation fails with 403, run `runtm-api auth status` and verify both the API key scopes and the user's org role.
