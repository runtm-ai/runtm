---
name: runtm
description: "Runtm (Runtime) Cloud, the platform where operations teams create and run AI agents, and its CLI runtm-api. Use for anything about runtm, runtime, runtm cloud, sessions, cloud sandboxes, templates, the agent roster, evaluation rubrics and scorecards, scheduled agents and cron, triggers (Slack, Linear, GitHub, Email, WhatsApp, SMS), skills, MCP servers, tool providers and integrations, guardrails, approvals, deployments, or when asked to build, create, or fix an agent (support agent, risk agent, underwriting agent). The method and every recipe live in the docs at https://docs.runtm.com/llms.txt; this file tells you how to read them and the few rules that fail silently."
metadata:
  version: "0.12.0"
  repository: https://github.com/runtm-ai/runtm
  tags: runtm,runtime,cli,agents,sandboxes
---

# Runtm (Runtime) Cloud

Runtime is where teams create and run agents that have a job, a way to measure success, tools, a runbook, triggers, and guardrails. The binary is `runtm-api` (not the pip `runtm` CLI, which handles local dev). It talks only to the hosted API at `https://app.runtm.com/api/cloud/...` and covers everything the dashboard does, so you can do anything a person does in the UI.

## Read the docs first

The documentation is the source of truth and is written to be read by you. Do not work from memory of this file when a page exists.

1. Fetch `https://docs.runtm.com/llms.txt`. It lists every page as `[Title](url.md): description`. The description says when to read the page and what you can do after; pick by description.
2. Fetch the page as markdown by appending `.md` to its URL, for example `https://docs.runtm.com/build/give-it-tools.md`.
3. Every dashboard step on a Build or Guides page carries its CLI equivalent in an agent-only block that appears only in the `.md` output: `cli: runtm-api ...` to run, `cli-verify: ...` to check it worked, and `cli-handoff: https://app.runtm.com/...` for a step only a person may do. For a handoff, fill in the placeholders, give the person the URL, and run the step's `cli-verify` once they say it is done.
4. No fetch tool in this sandbox? `runtm-api docs` prints `llms.txt`, and `runtm-api docs <path>` prints one page (`runtm-api docs build/give-it-tools`).

Where things are:

| You need | Read |
|---|---|
| The method for building any agent (six steps, in order) | `build/overview` and its six pages |
| A complete agent to copy (payments support, fraud and risk review, merchant underwriting) | `guides/overview`, `guides/payments/*` |
| One task (connect Stripe read-only, add a KYB provider, add an MCP server, route a Linear team, test a schedule, request an approval, delegate to another agent) | `guides/recipes/*` |
| Multi-agent: one agent handing work to subagents behind human approvals | `guides/recipes/delegate-to-another-agent`, `guides/payments/multi-agent-underwriting` |
| Driving a session, template lifecycle, debugging a stuck run, API patterns | `cloud-api/patterns/*` |
| Every command, flag, scope, and error | `cloud-api/agent-cli`, `cloud-api/scopes`, `cloud-api/errors`, then the endpoint pages |

## The method: capable first, then safe

An agent is built in six steps and the order matters. Steps 1 to 4 give it context and ability, step 5 proves it on seeded cases with read-only credentials, step 6 adds guardrails written from what the passing runs did. Never start with guardrails; a rule written before the first run is a guess, and on an unproven agent it hides capability gaps behind stalls.

1. **Define the job**: roster agent with description, system instructions, default template, coding agent.
2. **Measure success**: evaluation categories (when to use, pass or fail criteria, tags, human cost and time), monthly budget (observe-only). Without a category nothing is graded.
3. **Give it tools**: the template carries tool providers, MCP servers, secrets, repos. Credentials resolve agent, then personal, then org-wide; give every roster agent its own agent-scoped connection unless the job requires otherwise.
4. **Define how it works**: a runbook skill, the instruction layers, a trigger pointed at a test channel or a disabled schedule.
5. **Prove it works**: run seeded cases, read `session grade` and `agents scorecard`, tighten one thing at a time.
6. **Add guardrails and approvals**: allow, ask, deny rules from observed commands, network rules, `runtm-approval` at the runbook step that must wait.

Read `build/overview.md` before creating or changing an agent.

## Two golden paths

**Drive a sandbox yourself** (create a template, boot a session, run commands):

```bash
runtm-api template create --display-name "Dev env" --github-repo owner/repo --skip-agent   # --skip-agent = clone-only build, implies --build
runtm-api template get <template_id> | jq -r .build_status                                  # wait for "ready"
runtm-api session create --template-id <template_id>
runtm-api session exec <session_id> --json -- npm test                                      # {"stdout","stderr","exit_code"}
runtm-api session connect <session_id>                                                      # live PTY for a human
```

**Build an agent** (every capability hangs off one template):

```
trigger -> roster agent -> template -> { skills, MCP servers, tools, guardrails, context } -> session
```

Create the template, attach skills and MCP servers, verify with `template get`, build once, then create the roster agent pointing at the template, connect a test trigger, run the seeded cases, and only then add guardrails. Full recipe: `build/overview.md`; full worked examples: `guides/payments/*.md`.

## Rules that fail silently

- **Attached is not built.** Skills, MCP servers and guardrail rules attached after the last build are not in the snapshot. `template get | jq '{skills:[.skills[].name], stale:.attachments_changed_since_build}'` tells you; `template build` fixes it. `template create --skip-agent` implies `--build`, so attaching afterwards makes the template stale immediately.
- **A roster agent without `--template` runs on a bare sandbox** with no tools, skills or secrets.
- **A tool connection reaches a session only if an attached skill lists the provider in `requires.integrations`**, unless the connection is agent-scoped.
- **Never take a credential yourself.** Do not ask a person to paste an API key, token or password into the conversation, and do not pass one to `tools create --credentials`. Send them `https://app.runtm.com/connect?provider=<slug>&agent=<agent_id>&method=<auth_method_id>` so the secret goes straight to Runtime (agent scope is the default; use `scope=personal` or `scope=org` instead of `agent=` only when the job requires it), then confirm with `tools list --provider <slug>`. A secret already pasted to you is leaked: tell them to rotate it.
- **No evaluation categories means no grades**; the scorecard shows zeros.
- **Always `--json` on `session exec` when parsing.** The default PTY stream merges stderr into stdout and carries shell startup noise.
- **Scheduled agents: create `--disabled`, `run-now`, then `--enabled`.** `run-now` takes the identical path a cron tick takes. Cron is 5 fields in UTC with no per-agent time zone.
- **Delete an agent's triggers before the agent.**
- **Approvals are runbook steps, not settings.** They appear on the session card and in `session approvals list`, never in Slack.
- **Budget caps do not pause anything.** They flag on the scorecard and write an audit event.
- **Subagents are ordinary agents.** Delegate with `session launch --agent-id <uuid>` (or `session create --agent-id`), plus a `PreToolUse` gate hook when a human has to see it first; pass `--approval-id` when the callee's caller edge demands an approval. The callee's caller list decides who may launch it — naming any caller closes it to everyone else — and chains cap at 3 hops. Build and prove each child alone before wiring the orchestrator; children never delegate.

## Auth and org context

```bash
curl -fsSL https://runtm.com/install | bash        # installs runtm-api and this skill
export RUNTM_API_KEY=runtm_...                      # app.runtm.com > Settings > API Keys
runtm-api auth status | jq '{authenticated, scopes, organization_id}'
```

Org-scoped work (templates, agents, skills, MCP, tools, guardrails, team secrets) needs an **org-scoped key**. The org is bound when the key is created and cannot be changed at call time: `--org` or `RUNTM_ORG_ID` can only restate the key's binding. `organization_id: null` from `auth status` means a personal key, and the fix is a new org-scoped key, not an env var.

## Resolving inputs

1. Use what is in context (prior output, conversation, env vars).
2. Missing an id? Run the matching `list` or `get` first (`session list`, `template list`, `agents list`, `skills list`).
3. Still ambiguous? Ask the user.
4. Never run a command with an unresolved `<placeholder>`.

## Output contract

- JSON on stdout for every command. Errors on stderr as `{"error","status","hint"}`.
- Exit codes: `0` success, `1` API error, `2` auth error, `3` usage error.
- Streaming commands (`session prompt`, `session events`, `session deploy run`, `template build-logs`) emit JSON lines `{"event","data"}` and end with `event: "done"`.
- `401` key missing or invalid. `403` missing scope or personal key on an org resource (check `auth status`). `404` wrong id, run `list`. `409` name conflict. `422` body validation, check the endpoint page. `429` back off 5 to 10 seconds. `502` on `run-now` is the run failing with the reason in `detail`. `503` on scheduled-agent writes means no scheduler in this environment, create `--disabled` and use `run-now`. Full table: `cloud-api/errors.md`; scopes per command: `cloud-api/scopes.md`.

## Command areas

`runtm-api <area> --help` lists subcommands; nested trees exist (`session deploy --help`, `template guardrails --help`).

`auth`, `session`, `template`, `agents` (roster; `--type slack|github|linear|email` for triggers), `scheduled-agents`, `skills`, `mcp`, `tools`, `guardrails` (`rules|hooks|network` content plus `limits|allowlist` settings), `secrets`, `instructions`, `providers` (LLM keys), `groups`, `deployments`, `github`, `activity`, `docs`.

The full command table with flags is `cloud-api/agent-cli.md`.
