---
name: runtm-build-agent
description: "End-to-end recipe for assembling a working Runtm agent: plan the capabilities, create the template, attach skills/MCP servers/tools/guardrails, build once, then point a roster agent and a trigger at it. Use when the user wants to build an agent that DOES something (support triage, on-call, research, outbound, code review) rather than manage one primitive in isolation. Covers the order the pieces must be created in, and the silent failures that come from getting it wrong."
metadata:
  version: "0.1.0"
  tags: runtm,runtime,agent,build,assemble,template,skills,mcp,tools,guardrails,support-agent,recipe
---

# Build an agent on Runtm

Use this when the ask is **"build me an agent that does X"**: support triage,
on-call, research, outbound, code review. It spans templates, directives, the
roster, and triggers; the per-primitive skills (`runtm-templates`,
`runtm-integrations`, `runtm-agents`) go deeper on each piece.

## The capability chain

Everything an agent can do hangs off **one template**. Read this before
creating anything:

```
trigger          Slack / GitHub / Linear / Email / cron
  └─ roster agent      identity, system instructions, rubric, budget
       └─ template     <- THE capability carrier
            ├─ skills          how to do things (SKILL.md recipes)
            ├─ MCP servers     structured tool calls
            ├─ tools           stored provider credentials
            ├─ guardrails      what it may and may not run
            └─ context         instructions baked into the environment
                 └─ session    what actually runs when the trigger fires
```

The consequences:

- **A roster agent with no template can't do anything special.** `--template`
  is not a nicety; it is the entire answer to "what can this agent do".
- **A skill that is created but not attached to that template does nothing.**
  It exists in the org and is never loaded.
- **An attachment made after the last build does nothing until you rebuild.**
  Attaching changes config; the build bakes it into the snapshot.

Templates are not only for coding agents. A support agent's template usually
clones no repo worth speaking of and exists purely to carry its skills, MCP
servers, credentials, and guardrails.

## Order matters: build last

The single most common way to waste twenty minutes is to create things in the
order they come to mind. Plan first, attach everything, then build **once**.

```bash
export RUNTM_API_KEY=runtm_sk_live_...   # org-scoped; writes need admin/owner

# 0. PLAN. Decide the capability list before creating anything (see below).

# 1. Template first; everything attaches to it. Note: no --build yet.
TMPL=$(runtm-api template create \
  --name support-agent --display-name "Support Agent" \
  --github-repo acme/runbooks --tier standard | jq -r .id)

# 2. Capabilities. Prefer import: it attaches in the same call.
runtm-api skills import --source github_repo --uri acme/runbooks \
  --attach-template "$TMPL"

# `skills create` has NO attach flag, so it is always two steps.
SKILL=$(runtm-api skills create --name support-triage --md ./SKILL.md | jq -r .directive.id)
runtm-api skills attach "$SKILL" --template "$TMPL"

# Attach MERGES by default, so there is no need to read the current set first.
# Use --replace only when you mean to discard the other attachments.
runtm-api mcp attach "$MCP_ID" --template "$TMPL"

# 3. Guardrails for this template. Note the shape differs from the org-level
#    'guardrails rules create --kind deny --pattern ...': here it is a typed
#    content object.
runtm-api template guardrails create "$TMPL" \
  --name no-force-push --type allowlist \
  --content '{"kind":"deny","pattern":"git push --force*"}'

# 4. VERIFY BEFORE BUILDING. An empty skills list here means the build bakes nothing.
runtm-api template get "$TMPL" | jq '{
  skills: [.skills[].name],
  mcp: [.mcp_servers[].name],
  stale: .attachments_changed_since_build
}'

# 5. Build ONCE, now that everything is attached.
runtm-api template build "$TMPL" --skip-agent
runtm-api template get "$TMPL" | jq -r .build_status   # poll to "ready"

# 6. The roster agent, pointed at the template.
AGENT=$(runtm-api agents create --name "Support" \
  --instructions 'Triage inbound tickets. Never message a customer directly.' \
  --template support-agent --agent claude-code | jq -r .id)

# 7. A rubric, so runs get graded and the scorecard is not zeros.
runtm-api agents update "$AGENT" --evaluator-criteria \
  '{"objective":"Resolve or correctly escalate each ticket","checks":["cited a source","no customer contact"]}'

# 8. The trigger LAST, since it is what fires all of the above.
runtm-api agents create --type slack --name Support   # see preflight below
```

> `template create --skip-agent` implies `--build`. That fast path is right for
> a plain dev sandbox, but it fires the build **before** anything is attached.
> If you intend to attach skills, create without `--build` as above, or accept
> that you must rebuild afterwards.

## Step 0: plan the capability list first

Do not start creating directives until you can name what the agent needs and
which primitive carries each one. Ask the user, or infer from the job:

| The agent needs to... | Primitive | Command |
|---|---|---|
| Follow a written procedure | Skill | `skills create` / `skills import` |
| Call a service with structured tools | MCP server | `mcp create` |
| Authenticate to a service | Tool (knowledge integration) | `tools create`, or the dashboard for secrets |
| Be prevented from doing something | Guardrail | `guardrails rules` / `template guardrails` |
| Always be told something | Template context | `template context set` |

Check what already exists before authoring anything, because a good chunk of it is
usually already in the org:

```bash
runtm-api skills list                  # skills the org already has
runtm-api mcp list                     # MCP servers already defined
runtm-api tools providers list         # services already wired for credentials
runtm-api template list                # a template you can extend instead
runtm-api skills discover --repo acme/runbooks   # read-only; what a repo offers
```

For choosing **between** an MCP server, a skill wrapping a CLI, and a raw API
recipe, and for the auth trade-offs, use the `runtm-integrations` skill. That
decision is the one worth slowing down for; the rest of this file is mechanics.

## Failure modes

These fail **silently**: no error, just an agent that cannot do its job.

| Symptom | Cause | Fix |
|---|---|---|
| Agent runs but ignores its skills | Skills created, never attached | `skills attach <id> --template <t>`, then rebuild |
| `template get` shows the skills, sessions still don't have them | Attached after the last build | `attachments_changed_since_build: true` -> `template build` |
| Built template has an empty `skills` list | Built before attaching (often via `--skip-agent`) | Attach, then rebuild |
| Agent has no special behavior at all | Roster agent has no `--template` | `agents update <id> --template <slug>` |
| Scorecard is all zeros | No `evaluator_criteria` on the agent | `agents update <id> --evaluator-criteria '{...}'` |
| Trigger fires, but with none of the agent's identity | Trigger not bound to the roster agent | Triggers bind via `config.agent_id`: `--agent-id` on email, `--config '{"agent_id":"<roster_id>"}'` elsewhere |

Rebuilding after every single attach also wastes minutes and churns the
snapshot. Batch the attachments, then build once.

## Trigger preflight

Check these **before** calling `agents create --type ...`, so you fail with an
explanation instead of a bare 400:

- **Slack** needs the org's Slack *app configuration token*, set once in the
  dashboard. Without it, create returns 400. There is no CLI command to set it
  so send the user to the dashboard, then retry.
- **GitHub** cannot be a clickable link: the App-manifest flow must be POSTed,
  so the CLI writes an auto-submitting HTML page for you to open.
- **Linear** is headless only with `--linear-api-key` plus `--service-user`;
  the managed OAuth path returns an install URL to open.
- **Email** is fully headless. Check `GET /api/v1/email/status` if it 400s.

Slack, GitHub, and managed Linear all finish in a **browser**. When running
unattended, prefer Email or a scheduled agent (`runtm-automation`) and hand the
browser step back to the user.

## Verify it end to end

```bash
# What will a session from this template actually load?
runtm-api template get "$TMPL" | jq '{skills:[.skills[].name], stale:.attachments_changed_since_build}'
runtm-api template guardrails resolve "$TMPL"   # what it actually enforces
runtm-api template context resolve "$TMPL"      # what it is actually told

# Boot one and prove a capability works before wiring the trigger.
SID=$(runtm-api session create --template-id "$TMPL" | jq -r .id)
runtm-api session exec "$SID" --json -- 'ls ~/.claude/skills'
runtm-api session destroy "$SID"
```

Booting one session and checking the skills actually landed is worth more than
any amount of re-reading config.

## More

- `runtm-templates`: template lifecycle, session args, fix-sessions, secrets.
- `runtm-integrations`: choosing MCP vs skill vs CLI vs API, and auth methods.
- `runtm-agents`: roster fields, rubrics, budgets, scorecards, trigger config.
- `runtm-automation`: cron-driven agents instead of event-driven ones.
