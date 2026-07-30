---
name: runtm-integrations
description: "Create, read, update, and delete the Runtm Cloud session-context directives from the CLI: skills (SKILL.md bundles), MCP servers (stdio or http/sse), tools (knowledge integrations / provider credentials), and custom tool providers — and attach skills/MCP servers to org templates, repos, or all repos so sessions load them. Use when the user wants to add/connect/create an integration — ALWAYS follow the 5-step process: (1) research every way to reach the service (API, SDK, CLI, MCP, GitHub repos, predefined skills), (2) investigate the auth methods (OAuth, API key, service account) with pros/cons, (3) ask the user to pick the interface + auth combination, (4) build the definition (MCP server or tool provider), (5) redirect the user to connect in the dashboard UI so secrets never pass through the agent — or to author/manage skills, wire up an MCP server, attach a skill or MCP to a template, or define a NEW custom tool provider (name, logo, mise/npm/github package, and auth fields) when no built-in provider exists. Keeps three concepts distinct: a DEFINITION (tool provider / MCP server — the wiring, no secrets), a CONNECTION (the credentials a user supplies against a definition — entered in the dashboard UI, one definition can have many), and an ATTACHMENT (which templates/repos load it)."
metadata:
  version: "0.5.0"
  tags: runtm,runtime,directives,skills,mcp,tools,knowledge,templates,attachments,integrations
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

## All commands require an org-scoped API key

The org is read from the key; `--org` / `RUNTM_ORG_ID` cannot stand in for one,
and a personal key targeting an org is rejected with `403`.

```bash
export RUNTM_API_KEY=runtm_sk_live_...        # must be an org-scoped key
runtm-api auth status | jq .organization_id   # null => personal key
```

Writes need the right API-key scope: `context:write` for skills/MCP, and
`integrations:write` for tools. Reads need `context:read` / `integrations:read`.
Every command prints JSON to stdout and structured errors to stderr.

Common flags: `--page-size`, `--page-token` (list); `--include-content` (skill/
mcp list/get); `--yes` (delete confirmation). Each subcommand also accepts a raw
`--content` / `--credentials` JSON escape hatch for full control.

---

## Definition vs. connection vs. attachment (the mental model)

An integration is **three separate things** — keep them straight, because they
live in different places and only one of them ever touches a secret:

1. **Definition** — the *wiring*, no secrets. What the service is, how it's
   reached, and which auth method(s) it supports.
   - **Tool provider** (`tools providers`) — a service + its auth method(s) +
     the package to install. Either **managed** (a runtm-seeded provider) or
     **custom / forked** (org-owned). Lives at `/api/knowledge/providers`.
   - **MCP server** (`mcp`) — transport + command/url. An agent-directive of
     type `mcp_server_v0` at `/api/agent-directives`.

2. **Connection** — the *credentials* a person supplies **against** a definition
   (API key, OAuth token, service-account JSON). Stored **encrypted**, has a
   `status` (`active`/`error`/`revoked`/`pending`) and a **scope** (org-shared
   vs. personal). **One definition → many connections** — e.g. "Prod BQ" and
   "Dev BQ", or each teammate their own key.
   - For a **tool provider**: a *knowledge integration* —
     `/api/knowledge/integrations` (`runtm-api tools create`, or the dashboard).
   - For an **MCP server**: a *directive connection* —
     `/api/agent-directives/{id}/connections` (**dashboard only today** — the CLI
     can't create these yet; see the note under MCP servers).

3. **Attachment (context)** — *where* a skill or MCP server loads: specific
   templates, specific repos, or all repos.
   `/api/agent-directives/{id}/attachments` (`skills|mcp attach`). Creating a
   definition attaches it nowhere on its own.

Rule of thumb: **the agent builds definitions and attachments; the user creates
connections in the dashboard UI so secrets never pass through the agent** (Step 4
and Step 5 of the flow below). `tools create --credentials` and MCP
`--env`/`--header` *can* carry a secret for non-interactive automation, but that
is not the path for a secret a user is handing you — send them to the UI.

---

## Adding an integration: investigate → decide → build → connect in the UI

When the user wants to **add, connect, or create an integration** with an
external service (e.g. "connect Linear", "add Stripe", "wire up our data
warehouse"), do **not** jump straight to one mechanism. There are usually
several ways to reach a service, on several auth methods, each mapping onto a
different runtm primitive with different trade-offs. **Always work through the
five steps below**: research the options, weigh the auth methods, let the user
choose the combination, build the *definition*, then hand off to the dashboard
UI for the actual credential connect. Never paste real secrets through the CLI.

### Step 1 — Research everything the service offers

Google / read the vendor's developer docs and GitHub. Enumerate **every** way
the service can be reached — is there an API, an SDK, a CLI, an MCP server,
useful GitHub repos, or a ready-made skill? Each maps onto a runtm primitive:

| Access modality | What to look for | runtm primitive | Pros | Cons |
|---|---|---|---|---|
| **MCP server** | An official or community MCP server (installable stdio package, or hosted http/sse endpoint) | `runtm-api mcp create` | Native tool-calling, structured, maintained by others, least glue code | Not every service has one; adds a server process; you inherit its tool design & quality |
| **Predefined skill** | An existing `SKILL.md` (bundled, community, or a GitHub repo you can import) | `runtm-api skills create` / import | Ready-made; reuse over authoring; encodes best-practice recipes | May be stale or partial; still needs a credential path |
| **CLI** | An official command-line tool | `runtm-api skills create` (document the commands) + a credential via `tools` / a custom provider whose `mise` package installs the binary | Uses battle-tested official tooling; easy for the agent to drive; great coverage | Agent drives free-text commands (less structured); the binary must be installed in the sandbox |
| **SDK** | An official client library (npm/pip/…) | `runtm-api skills create` (document usage) + install the package + a credential via `tools` | Idiomatic, typed, well-documented; scriptable | You write the glue; language-specific; package must be installed |
| **REST / HTTP API** | The public API + auth docs (almost always exists) | `runtm-api skills create` (document the endpoints/recipes) + an API key via `tools` or a session/template secret | Maximum control & coverage; no extra process | You hand-author request recipes; more upfront work; you own the maintenance |

Prefer an existing **MCP server** or **predefined skill** when one exists and is
good; fall back to **CLI-via-skill**, then **SDK/API-via-skill**. Surface all
viable options — the user may have a preference.

### Step 2 — Investigate the auth methods and weigh each

For each viable modality, list how it can authenticate and weigh the trade-offs
— don't assume one. Common methods and their pros/cons:

| Auth method | Pros | Cons | runtm path |
|---|---|---|---|
| **OAuth** | No long-lived secret to paste; per-user identity; revocable; scoped consent | Needs an OAuth app (built-in or bring-your-own); browser flow only | Dashboard connect; BYO app via `tools providers … --auth-methods '[{… "kind":"oauth" …}]'` |
| **API key / token** | Simplest; works headless; easy to reason about | Long-lived shared secret; often coarse scope; manual rotation | `tools` field / custom provider secret field |
| **Service account file** | Good for machine-to-machine (GCP, etc.); fine-grained IAM | JSON key to store & rotate; powerful if leaked | `tools` credential field (e.g. `service_account_json`) |

Note the **scope**: org-wide (define the provider once, every teammate connects
their own credential) vs. personal / one session. Check whether the service is
already a known **tool provider** (`runtm-api tools providers list`) before
defining a new one.

### Step 3 — Present the options and let the user pick the combination

Summarize the modalities × auth methods with their pros/cons and **ask the user
to choose the interface + auth combination** before you build anything —
for example:

> Linear can be integrated three ways:
> 1. **MCP server** (hosted, http/sse) — auth via OAuth (dashboard) or an API
>    key header. Most native; least code.
> 2. **`linear` CLI** wrapped as a skill — auth via personal API key. Uses the
>    official CLI; good for scripted workflows.
> 3. **REST API** documented in a skill — auth via API key. Most control.
>
> Which approach do you want, and which auth method?

Skip the question only when the user has already named the exact mechanism and
auth combination.

### Step 4 — Build the *definition* (MCP server or tool provider)

Once the user has chosen, create the **definition** — the wiring, never the
secret:

- **MCP server** → `runtm-api mcp create …`
- **New tool provider** (nothing built-in matches) →
  `runtm-api tools providers create …` — define its auth `fields`,
  `materialization`, and `--logo`. Org-scoped, so the whole team can then connect.
- **Skill** (CLI / SDK / API recipes, or an imported predefined one) →
  `runtm-api skills create …`

### Step 5 — Hand off to the dashboard UI to connect (never handle secrets)

**Do not paste real credentials through the CLI or agent.** After the definition
exists, redirect the user to connect in the dashboard UI — the **Integrations**
page on the runtm dashboard (the `/home` app) — so credentials are entered in
the browser and stored server-side; the agent never sees them. This holds for
**every** auth method: OAuth *requires* the browser flow, and API keys /
service-account files should also be entered via the UI, not `--credentials`.

> The <provider> integration is defined. Connect it in the dashboard →
> **Integrations** → <provider> → **Connect**, and sign in / paste your
> credential there. I won't handle the secret directly.

(`tools create --credentials …` exists for non-interactive automation where a
secret is already available to the caller — it is **not** the path for a user
handing you a fresh secret. Default to the UI connect.)

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

### Skills at org scale: import, discover, resync, lock

Creating skills one at a time by hand does not scale. The lifecycle verbs do:

```bash
# See what a repo contains before importing (read-only, nothing is created)
runtm-api skills discover --repo acme/agent-skills
# -> candidates: [{path, name, description, already_imported}, ...]

# Bulk-import from a repo, attaching in the same call so nothing lands
# unattached and silently unused
runtm-api skills import --source github_repo --uri acme/agent-skills \
  --attach-template <tmpl_id>
# Or import one file by URL:
runtm-api skills import --source github_url \
  --uri https://github.com/acme/agent-skills/blob/main/deploy/SKILL.md

# Re-pull a skill from its original source, keeping id + attachments.
# No-op when the source is unchanged (response carries no_changes).
runtm-api skills resync <id>

# Protect a critical skill from edits (org admin only). Locked skills still
# load into sessions and builds; edits/deletes/resync/attachments are blocked.
runtm-api skills lock <id>
runtm-api skills unlock <id>

# What labels exist, with counts (before filtering a list by one)
runtm-api skills facets [--template <tmpl_id>] [--repo owner/name]

# Add a binary or oversized file to a skill bundle via object storage
# (presign, PUT the bytes, patch the manifest; max 5 MiB)
runtm-api skills upload-file <id> --file ./data/lookup.csv --path data/lookup.csv
```

`resync`, `lock`, `unlock`, and `facets` also exist on `runtm-api mcp`.
Group ownership: `runtm-api skills update <id> --owner-team <team_id>` limits
visibility to one group; empty string makes it org-wide again.

---

## MCP servers

Two transports. **stdio** launches a local command; **http**/**sse** points at a
remote URL.

> **Definition vs. connection for MCP.** `--env`/`--header` bake a value **into
> the server definition** — fine for a shared, non-secret setting, but a real
> per-user credential belongs in a **directive connection** (entered in the
> dashboard, kept out of the definition and away from the agent). The CLI can't
> create directive connections yet, so for a secret-bearing MCP server: create
> the definition here **without** the secret, then send the user to the dashboard
> **Integrations** page to connect it (Step 5).

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

## Attaching skills & MCP servers to templates (and repos)

Creating a skill or MCP server does **not** load it anywhere on its own — it
only loads into a session where it is **attached**. Attach it to an **org
template** and every session launched from that template loads it. This is the
CLI equivalent of the dashboard's "Attach to template" picker.

`skills` and `mcp` both expose the same three verbs (same
`/api/agent-directives/{id}/attachments` endpoint, `context:write` to change):

```bash
# Attach a skill to a template (sessions from that template now load it)
runtm-api skills attach <skill_id> --template <template_id>

# Attach an MCP server to two templates at once
runtm-api mcp attach <mcp_id> --template <t1> --template <t2>

# Attach to specific repos, or to every repo in the org
runtm-api skills attach <skill_id> --repo acme/api --repo acme/web
runtm-api skills attach <skill_id> --all

# See where something is attached
runtm-api skills attachments <skill_id>

# Detach from one template (leaves other attachments in place)
runtm-api skills detach <skill_id> --template <template_id>
# Remove every attachment
runtm-api skills detach <skill_id> --clear
```

Three equivalent ways to ask "what does this template load?" — use whichever
you reached for first:

```bash
runtm-api template skills <template_id>          # template-first
runtm-api skills list --template <template_id>   # skills-first
runtm-api template get <template_id> | jq .skills   # inline, plus staleness
```

The same three exist for MCP servers (`template mcp`, `mcp list --template`,
`template get | jq .mcp_servers`). Scope a listing to a repo instead with
`--repo owner/name`. Every scoped form also includes org-wide items (those
attached with `--all`), because those load too.

Scopes & semantics:

- Three scopes, **mutually exclusive with `--all`**: `--template` (repeatable),
  `--repo owner/name` (repeatable), and `--all` (every repo in the org). You can
  mix `--template` and `--repo` in one call; `--all` cannot be combined with
  either and supersedes them.
- `attach` **merges** with the current scope by default (repeated calls add).
  Pass `--replace` to set the exact scope wholesale. Switching to `--all`
  replaces any scoped attachments.
- `detach` removes the named `--template`/`--repo`, `--all` removes the
  all-repos attachment, and `--clear` removes everything. It leaves untouched
  attachments in place.
- Only **org-owned** skills/MCP servers can be attached (they come from an
  org-scoped key); personal directives cannot.

The full flow to wire a skill into a template. **Creating a skill attaches it
nowhere and bakes it nowhere** — both follow-up steps are required, and both
fail silently if skipped:

```bash
export RUNTM_API_KEY=runtm_sk_live_...   # org-scoped key
SKILL_ID=$(runtm-api skills create --name deploy-checks --md ./SKILL.md | jq -r .directive.id)

# 1. Attach it — without this, sessions boot without the skill
runtm-api skills attach "$SKILL_ID" --template <template_id>

# 2. Verify, and check whether the snapshot is now behind the config
runtm-api template get <template_id> | jq '{skills: [.skills[].name], stale: .attachments_changed_since_build}'

# 3. Rebuild if stale — attaching changes config, not the built image
runtm-api template build <template_id>
```

`attachments_changed_since_build: true` means the attachments moved after the
last build, so sessions still boot with the old set until you rebuild. That
flag is the reason to prefer `template get` over `template skills` when you are
about to trust a template.

**Attach everything, then build once.** Rebuilding after each attach costs
minutes per round and churns the snapshot for anyone booting sessions
meanwhile. When wiring several skills, attach them all, check `template get`
once, then issue a single `template build`. Same for the reverse order trap:
`template create --skip-agent` implies `--build`, so creating a template that
way and attaching afterwards always leaves you a rebuild in debt. Assembling a
whole agent rather than adding one integration? Use the `runtm-build-agent`
skill, which sequences this end to end.

---

## Tools (knowledge integrations)

Stored provider credentials (e.g. `bigquery`, `notion`). This command handles
**static-credential** providers (service accounts, API keys); OAuth providers are
connected through the dashboard.

> **Prefer the dashboard UI to connect.** When a *user* is handing you a fresh
> secret, don't take it — point them at the dashboard **Integrations** page to
> enter it (see Step 5 above). `--credentials` below is for non-interactive
> automation where the caller already holds the secret, not for accepting one
> from the user.

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

Then any teammate connects with their own key **in the dashboard** — the new
provider now shows up on the **Integrations** page, where each person enters
their own credential in the browser (secrets never touch the agent). The
equivalent non-interactive call, only when the caller already holds the secret,
is:

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
runtm-api skills attach --help    # attach/detach/attachments verbs
runtm-api template skills --help  # template-side view of attachments
```
