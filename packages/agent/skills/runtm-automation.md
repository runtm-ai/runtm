---
name: runtm-automation
description: "Schedule Runtm Cloud agents to run a prompt on a cron — create, verify with run-now, enable, and debug a schedule that did not fire. Use when the user wants recurring/automated agent runs, a weekly or nightly job, a cron agent, a Slack digest on a schedule, or asks why a scheduled agent produced nothing."
metadata:
  version: "0.1.0"
  tags: runtm,runtime,scheduled-agents,cron,automation,recurring
---

# Runtm Scheduled Agents

`runtm-api scheduled-agents` runs a prompt on a clock. Each agent pairs a cron
expression with a prompt, an optional org template, and an optional Slack
channel to post the result to. Every tick launches a fresh session in autopilot
mode and runs the prompt to completion.

Not to be confused with `runtm-api agents`, which manages Slack/GitHub bots that
fire on an **event**. These fire on a **schedule**.

| Verb | Command |
|------|---------|
| List (with `next_run_at`) | `runtm-api scheduled-agents list` |
| Get one | `runtm-api scheduled-agents get <id>` |
| Create | `runtm-api scheduled-agents create --name X --cron '...' --prompt '...'` |
| **Run once now** | `runtm-api scheduled-agents run-now <id>` |
| Enable / disable | `runtm-api scheduled-agents update <id> --enabled\|--disabled` |
| Edit | `runtm-api scheduled-agents update <id> [flags]` |
| Delete | `runtm-api scheduled-agents delete <id> --yes` |

Aliases: `schedules`, `cron`. `run-now` also answers to `run` and `trigger`.

## Org context + scope

Org-scoped, so an **org-scoped API key** is required — the org is read from the
key and `--org` / `RUNTM_ORG_ID` cannot substitute for one. Reads need
`sessions:read`; create/update/delete/run-now need `sessions:write` **and an
admin or owner role**, because a run costs compute and can post to a shared
channel.

## The order that works: create disabled → run-now → enable

A scheduled agent that is wrong fails at its scheduled hour, into a channel,
with nobody watching. `run-now` executes the identical code path the cron tick
takes — same template resolution, same Slack target, same orchestrator call —
so build the habit of proving it first:

```bash
# 1. Create it switched off so the schedule can't fire before you've checked it
runtm-api scheduled-agents create \
  --name weekly-outbound \
  --disabled \
  --cron '0 18 * * 1' \
  --template 28f6e6e6-d73d-4f21-8b1f-312e17e8f47b \
  --prompt 'Build this week'"'"'s outbound lists and post them for approval' \
  --slack-integration <integration_id> \
  --slack-channel C0123456789
# -> {"id": "9f3c...", "enabled": false, "next_run_at": null, ...}

# 2. Prove it. Returns the launched session_id; a bad template or missing
#    integration surfaces here as a 502 with the reason.
runtm-api scheduled-agents run-now 9f3c...
# -> {"session_id": "a641...", "enabled": false, "trigger": "manual", ...}

# 3. Watch the run you just triggered
runtm-api session status a641...
runtm-api session history a641...

# 4. Only once the output is what you want, turn the schedule on
runtm-api scheduled-agents update 9f3c... --enabled
runtm-api scheduled-agents get 9f3c... | jq .next_run_at
```

`run-now` deliberately works on disabled agents — that is what makes this order
possible. It stamps `last_run_at` / `last_session_id` like any other run.

## Cron is 5 fields, in UTC, with no per-agent time zone

Cloud Scheduler runs every job in `Etc/UTC`. There is no per-agent time zone
setting, so a local time has to be converted by hand — and revisited when
daylight saving shifts:

| Intent | Winter (PST, UTC-8) | Summer (PDT, UTC-7) |
|--------|--------------------|--------------------|
| Daily 11am Pacific | `0 19 * * *` | `0 18 * * *` |
| Mondays 11am Pacific | `0 19 * * 1` | `0 18 * * 1` |
| Every hour | `0 * * * *` | `0 * * * *` |
| Weekdays 9am Pacific | `0 17 * * 1-5` | `0 16 * * 1-5` |

Confirm the schedule reads the way you meant by checking `next_run_at`, which
the API derives from the cron:

```bash
runtm-api scheduled-agents list | jq '.agents[] | {name, cron, enabled, next_run_at, last_run_at}'
```

`next_run_at` is `null` when the agent is disabled — a disabled agent has no
Scheduler job, so there is no next run to report.

## Debugging "it was supposed to run and nothing happened"

Work down this list; each step rules out one layer.

```bash
# 1. Is the schedule even live? enabled=false or next_run_at=null means no job.
runtm-api scheduled-agents get <id> | jq '{enabled, cron, next_run_at, last_run_at, last_session_id}'

# 2. Did a tick fire? Compare last_run_at against the time you expected.
#    Unchanged => the schedule never fired (check enabled + the UTC conversion).
#    Updated   => it fired and the failure is inside the run; go to step 4.

# 3. Reproduce on demand. A 502 here carries the reason the tick would fail.
runtm-api scheduled-agents run-now <id>

# 4. Read what the run actually did.
runtm-api session history <last_session_id>
runtm-api session status <last_session_id>
```

Common causes, in rough order of frequency:

| Symptom | Cause | Fix |
|---------|-------|-----|
| `next_run_at` is null | Agent disabled | `update <id> --enabled` |
| Fired an hour early/late | Daylight saving moved the UTC offset | Re-derive the cron from the table above |
| `502` from `run-now` | The launch itself fails (unknown template, deleted Slack integration) | Read `detail`; fix the referenced resource |
| `503` from `create`/`update` | Cloud Scheduler isn't configured in this environment | Create `--disabled` and drive it with `run-now` |
| Ran, but nothing reached Slack | No Slack target configured, or the run errored before finishing | `get <id> \| jq '{slack_integration_id, slack_channel_id}'`, then `session history` |
| Run started but never finishes | Stalled on an approval gate (autopilot runs can hit them) | `runtm-api session approvals list <last_session_id>`, then `approvals resolve ... --approve\|--reject` |
| Ran and finished, but was it good? | Nothing wrong; you want the verdict | `runtm-api session grade <last_session_id>`; aggregate with `runtm-api agents scorecard` |
| Ran, output was wrong | The prompt or template is wrong, not the schedule | Iterate with `run-now`, not by waiting for ticks |

## Slack output

The integration and channel travel together — passing one without the other is
rejected. When both are set, each run's result is posted to the channel as that
integration's Slack app.

```bash
runtm-api scheduled-agents update <id> \
  --slack-integration <integration_id> \
  --slack-channel C0123456789
```

Find integration ids with `runtm-api agents list --type slack`.

Runs are unattended (autopilot), so the prompt should say exactly what to
produce and in what shape. A prompt that ends "post the Block Kit JSON" gets
raw JSON pasted into the channel; say "post a short plain-text summary" if that
is what you want a human to read at 11am.

## Editing a schedule

`update` only sends the flags you pass, so a partial edit never clobbers the
prompt or cron it did not mention:

```bash
runtm-api scheduled-agents update <id> --cron '0 18 * * 1'   # DST shift
runtm-api scheduled-agents update <id> --prompt 'New instructions'
runtm-api scheduled-agents update <id> --disabled            # stop, keep the agent
```

Prefer `--disabled` over `delete` when pausing: it removes the Scheduler job but
keeps the prompt, template binding, and Slack target so re-enabling is one call.

## Related skills

- `runtm-templates` — build the template a scheduled agent launches from, and
  verify its skills are attached and baked (`attachments_changed_since_build`).
- `runtm-agents` — Slack/GitHub agents that fire on events instead of a clock.
- `runtm-sessions` — inspect the sessions a scheduled run creates.
