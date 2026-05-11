---
name: runtm-debug
description: "Investigate and debug Runtm Cloud sessions: inspect filesystem, read prompt history, follow live events, dump workspace state, check env vars, surface errors. Use when a user reports 'my session is stuck', 'something broke', 'why didn't the agent do X', or wants a status snapshot."
metadata:
  version: "0.1.0"
  tags: runtm,runtime,debug,session,investigation
---

# Runtm Session Debug

When a customer says something is wrong with a session ("the agent is stuck", "the build keeps failing", "what did Claude do?"), this skill walks you through gathering enough state to either fix it or hand it back to the human with full context.

## Step 1: identify the session

If the user gives you a session ID, use it. Otherwise discover the most recent activity:

```bash
runtm session list --limit 5             # all recent sessions
runtm activity recent-prompts --limit 5  # most recent prompts across the org
```

## Step 2: capture full session state in one pass

Pipe these calls together to get a complete snapshot:

```bash
SID=<session_id>

# Canonical detail (state, agent, template, sandbox URL, timestamps)
runtm session get "$SID"

# Polling envelope (last_prompt status + cost + summary)
runtm session status "$SID"

# Workspace state (dirty files, open tabs, env, auth, instructions)
runtm session workspace-state "$SID"

# Per-session instructions (the CLAUDE.md the agent was reading)
runtm session instructions get "$SID"

# Prompt history (all prompts run in this session)
runtm session history "$SID"

# Effective env vars (names + sources, values masked)
runtm session env get "$SID"
runtm session env detected "$SID"
```

This is the equivalent of opening the session in the dashboard and inspecting every panel.

## Step 3: look at filesystem state

```bash
# List top-level files
runtm session file list "$SID" --path /home/user/project

# Search for something specific
runtm session file search "$SID" --query "TODO" --path /home/user/project

# Read a specific file
runtm session file read "$SID" /home/user/project/package.json
```

## Step 4: follow live events (if prompt is mid-run)

```bash
# Live SSE stream of the agent's tool use, output, and result events.
# Stops automatically when the prompt finishes.
runtm session events "$SID"
```

Each stdout line is a JSON envelope:

```
{"event":"text_delta","data":{...}}
{"event":"tool_use","data":{"name":"Edit","input":{...}}}
{"event":"tool_result","data":{...}}
{"event":"result","data":{"cost_usd":0.03,"summary":"..."}}
{"event":"done","data":{...}}
```

## Step 5: interpret common failure modes

| Symptom | Likely cause | Next move |
|---------|--------------|-----------|
| `state: error` + `error_message` set | Sandbox failed to provision (E2B / template / secrets) | Check `runtm template get <tmpl_id>` for `has_all_required: false`; verify team secrets via `runtm secrets list --team`. |
| `state: paused` with no recent activity | Auto-paused after 20 min idle | `runtm session resume <id>` then re-prompt. |
| `last_prompt.status: timed_out` | Prompt hit `prompt_timeout_minutes` cap | Use `runtm session prompt <id> "..."` to re-run, or split into smaller steps. |
| `last_prompt.status: error` | Agent / model error | Check `last_prompt.error` field; check `runtm guardrails can-deploy` for org policy issues. |
| Prompt running forever, no tool output | Stuck / hung | `runtm session prompt-cancel <id>`, then `runtm session status` to confirm. |
| Files modified but no PR | Agent didn't push | Run `runtm session git <id> status` then `runtm session git <id> create_branch_and_pr --pr-title "..."`. |
| Build failing repeatedly | Template environment broken | Use `runtm template fix-session <tmpl_id>` to enter the sandbox and repair (see runtm-templates skill). |

## Step 6: take corrective action

```bash
# Cancel a runaway prompt
runtm session prompt-cancel "$SID"

# Rewind to a prior prompt (drop the failed one from history)
runtm session prompt-rewind "$SID" --to-index 3

# Update per-session instructions before re-prompting
runtm session instructions set "$SID" --text "Always run `npm test` before committing."

# Pause to stop spending compute while you investigate
runtm session pause "$SID"

# Force-destroy if it's truly hung
runtm session destroy "$SID"
```

## Step 7: when to escalate to the human

Stop and ask the user before:

- Deleting a session that has uncommitted work (check `workspace-state`'s dirty files first)
- Calling `runtm guardrails cleanup --yes` (destroys ALL stuck org sessions)
- Rewinding history past the user's last manual prompt
- Saving a snapshot of a fix-session that isn't fully verified

## Multi-session sweep

If the user asks "what's going on across all our sessions?", chain:

```bash
runtm session list --limit 50 | jq '.sessions[] | select(.state == "error" or .state == "running") | {id, state, agent, name}'
runtm activity team-summary | jq '{total_sessions, total_cost_usd, total_prompts}'
runtm guardrails can-deploy
```

That gives the user a one-screen overview equivalent to the dashboard's activity tab.
