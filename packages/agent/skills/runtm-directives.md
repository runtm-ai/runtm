---
name: runtm-directives
description: "Create, read, update, and delete the Runtm Cloud session-context directives from the CLI: skills (SKILL.md bundles), MCP servers (stdio or http/sse), tools (knowledge integrations / provider credentials), and custom tool providers. Use when the user wants to author/manage skills, wire up an MCP server, store provider credentials, or define a NEW custom tool provider (name, logo, mise/npm/github package, and auth fields) when no built-in provider exists."
metadata:
  version: "0.2.0"
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

## Custom tool providers

`tools providers …` defines a brand-new tool *provider* (the thing behind
`tools`): its **name**, **logo**, the **package** to install (a mise spec —
`latest`, `npm:pkg`, `github:owner/repo`, `cargo:crate`, `ubi:owner/repo`), and
its **auth mechanism(s)**. Pure API — no browser; needs an org-admin key with
`integrations:write`. (`tools create` stores credentials *against* a provider;
this defines the provider itself.)

### When to create one

**If the tool the user wants isn't already a provider, create a custom one.**
First check: `runtm-api tools providers list` (and the `tools`/catalog). If
nothing matches, define it here. A custom provider is **org-scoped**, so once
you create it *every member of the team* can connect to it — you define it once,
everyone connects their own credentials.

Two things matter when defining it:

1. **Use `fields` for the secrets** so the whole team can connect. The auth
   method's `fields[]` are the inputs each person fills in (API key, token, …);
   mark secrets with `"kind":"secret"`. `materialization.env`/`files` then inject
   those values into the sandbox (via `{fields.<id>}`). Because the provider is
   org-wide, defining the fields once lets any teammate run
   `tools create --provider <slug> --auth-method <id> --credentials '{…}'` with
   their own values — no need to redefine the provider per person.
2. **Always pass `--logo` (recommended).** A brand logo URL makes the provider
   card look right in the dashboard; without it the card falls back to a generic
   glyph. Pass an absolute `https://…` image URL.

### Example: a "Pylon" provider (npm CLI + user-entered API key)

```bash
runtm-api tools providers create \
  --slug pylon \
  --name "Pylon" \
  --category support \
  --logo https://app.pylon.com/favicon.png \
  --package pylon-cli=npm:pylon-cli \
  --auth-methods '[{
    "id": "api_key",
    "display_name": "API Key",
    "kind": "static",
    "fields": [
      { "id": "api_key", "label": "API Key", "kind": "secret", "required": true }
    ],
    "materialization": { "env": { "PYLON_API_KEY": "{fields.api_key}" } }
  }]'
```

Then any teammate connects with their own key:

```bash
runtm-api tools create --provider pylon --auth-method api_key \
  --credentials '{"api_key": "..."}'
```

### Commands

```bash
runtm-api tools providers list
runtm-api tools providers get <id>
runtm-api tools providers update <id> --logo https://example.com/new.png   # fetches current schema, applies the change
runtm-api tools providers delete <id> --yes
runtm-api tools providers fork <built-in-id> --slug my-notion              # editable copy, e.g. to bring your own OAuth app
```

Flags build a `ProviderSchema`: `--name` (display_name, required), `--logo`
(image_url), `--icon`, `--tagline`, `--description`, `--category`, repeatable
`--package NAME=SPEC` (→ `tooling.mise`), and `--auth-methods <json>` (the
auth_methods array — at least one required). For full control pass the whole
schema with `--schema '<json>'` or `--schema-file <path>` (flags override on
top); `--oauth-secrets '{"<method>":{"client_id":"…","client_secret":"…"}}'`
attaches per-method OAuth app credentials.

`auth_methods[]` entries: `id`, `display_name`, `kind` (`static`|`oauth`),
`fields[]` (the credential inputs the user fills), `materialization`
(`env`/`files`/`setup_commands` written into the sandbox, interpolating
`{fields.x}` / `{oauth.access_token}`), optional `oauth` (`auth_url`,
`token_url`, `scopes`, …) and `health` probe.

Tip: discover package specs with the package search endpoint
(`GET /api/knowledge/package-search?backend=mise|npm|cargo|homebrew|github&q=…`)
— each hit returns the `mise_spec` to drop into `--package`.

---

## Discovery

```bash
runtm-api skills --help
runtm-api mcp --help
runtm-api tools --help
runtm-api tools providers --help
runtm-api mcp create --help
```
