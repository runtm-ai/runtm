---
name: runtm-directives
description: "Create, read, update, and delete the three Runtm Cloud session-context directives: skills (SKILL.md bundles), MCP servers (stdio or http/sse), and tools (knowledge integrations / provider credentials). Use when the user wants to author or manage skills, wire up an MCP server, or store provider credentials from the CLI."
metadata:
  version: "0.1.0"
  tags: runtm,runtime,directives,skills,mcp,tools,knowledge
---

# Runtm Directives

CRUD for the three things the dashboard's **Session context** picker manages.
Each has its own top-level command. Reference: https://docs.runtm.com/cloud-api.

| Command | What it manages | Endpoint family | Type |
|---|---|---|---|
| `skills` | Agent skill bundles (SKILL.md + files) | `/api/agent-directives` | `skill_v0` |
| `mcp` | MCP servers (stdio or http/sse) | `/api/agent-directives` | `mcp_server_v0` |
| `tools` | Knowledge integrations (provider credentials) | `/api/knowledge/integrations` | — |

Each command exposes the same verbs: `create`, `get`, `list`, `update`,
`delete`. Skills and MCP servers happen to share one backend endpoint
(distinguished by `type`); tools are knowledge integrations on a separate
endpoint. That mapping is an implementation detail — just use `skills`, `mcp`,
and `tools`.

## All commands require org context

```bash
export RUNTM_ORG_ID=org_abc123    # or pass --org org_abc123 each time
```

Writes need the right API-key scope: `context:write` for skills/MCP, and
`integrations:write` for tools. Reads need `context:read` / `integrations:read`.
Every command prints JSON to stdout and structured errors to stderr.

Common flags: `--page-size`, `--page-token` (list); `--include-content` (skill/
mcp list/get); `--yes` (delete confirmation). Each subcommand also accepts a raw
`--content` / `--credentials` JSON escape hatch for full control.

---

## Skills

A skill is a `SKILL.md` (plus optional extra files). The fast path passes the
markdown file directly with `--md`; the CLI wraps it into the content payload.

```bash
# Create from a local SKILL.md
runtm-api skills create \
  --name deploy-checks \
  --display-name "Pre-deploy checks" \
  --description "Run lint, typecheck, and tests before deploy" \
  --md ./SKILL.md

# Full control: pass the raw skill content object
runtm-api skills create --name deploy-checks --content '{
  "entry_md": "SKILL.md",
  "files": [{"path": "SKILL.md", "mode": "text", "inline": "# Deploy checks\n..."}],
  "requires": {"integrations": ["bigquery"], "tooling": {"mise": {"gcloud": "latest"}}}
}'

# List / get / update / delete
runtm-api skills list --include-content
runtm-api skills get <id>
runtm-api skills update <id> --display-name "New name"
runtm-api skills update <id> --md ./SKILL.md   # replace body
runtm-api skills delete <id> --yes
```

Skill content fields: `entry_md` (default `SKILL.md`), `files[]`
(`{path, mode: text|binary, inline}`), optional `runtime_env`, `frontmatter`,
and `requires` (`{integrations: [...], tooling: {mise: {...}}}`).

---

## MCP servers

Two transports. **stdio** launches a local command; **http**/**sse** points at a
remote URL.

```bash
# stdio server
runtm-api mcp create \
  --name files \
  --display-name "Filesystem MCP" \
  --transport stdio \
  --command npx --arg -y --arg @modelcontextprotocol/server-filesystem \
  --env ROOT=/home/user/project

# http/sse server
runtm-api mcp create \
  --name remote-tools \
  --transport http \
  --url https://mcp.example.com \
  --header "Authorization=Bearer $TOKEN"

# Full control
runtm-api mcp create --name files --content '{
  "transport": "stdio",
  "command": "npx",
  "args": ["-y", "@scope/server"],
  "env": {"TOKEN": "abc"}
}'

# List / get / update / delete
runtm-api mcp list
runtm-api mcp get <id> --include-content
runtm-api mcp update <id> --url https://new.example.com
runtm-api mcp delete <id> --yes
```

Content shape — stdio: `{transport:"stdio", command, args[], env{}}` (command
required). http/sse: `{transport:"http"|"sse", url, headers{}}` (url required).
`--arg` is repeatable and ordered; `--env`/`--header` take `KEY=VALUE`.

---

## Tools (knowledge integrations)

Stored provider credentials (e.g. `bigquery`, `notion`). This command handles
**static-credential** providers (service accounts, API keys); OAuth providers are
connected through the dashboard.

```bash
# Create
runtm-api tools create \
  --provider bigquery \
  --auth-method service_account \
  --scope org \
  --display-name "Production warehouse" \
  --credentials '{"service_account_json": "{...}"}' \
  --provider-metadata '{"project_id": "my-gcp-project"}'

# List / get / update / delete
runtm-api tools list --scope org --provider bigquery
runtm-api tools get <id>
runtm-api tools update <id> --default-mode ask
runtm-api tools delete <id> --yes
```

Create fields: `--provider`, `--auth-method`, `--credentials` (all required),
`--scope` (`org`|`personal`, default `org`), `--display-name`,
`--provider-metadata`, `--default-mode`. Update accepts `--display-name`,
`--provider-metadata`, `--default-mode`, `--tool-permissions` (JSON).

---

## Discovery

```bash
runtm-api skills --help
runtm-api mcp --help
runtm-api tools --help
runtm-api mcp create --help
```
