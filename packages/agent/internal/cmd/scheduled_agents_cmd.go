package cmd

import (
	"fmt"
	"net/url"

	"github.com/spf13/cobra"
)

// NewScheduledAgentsCommand returns `runtm-api scheduled-agents` — cron-driven
// agents, the automation primitive.
//
// Distinct from `runtm-api agents`, which manages Slack/GitHub integration
// agents that fire on an *event*. These fire on a *schedule*.
//
// Routes (all org-scoped, under /api/v1/scheduled-agents):
//
//	GET    ""                sessions:read    (list)
//	GET    "/{id}"           sessions:read    (get)
//	POST   ""                sessions:write   (create, admin)
//	PATCH  "/{id}"           sessions:write   (update, admin)
//	POST   "/{id}:run"       sessions:write   (run now, admin)
//	DELETE "/{id}"           sessions:write   (delete, admin)
//
// `run-now` exists so a schedule can be validated on demand: it executes the
// identical path the cron tick takes, so a bad template or missing Slack
// target fails in front of you instead of silently at the scheduled hour.
func NewScheduledAgentsCommand(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:     "scheduled-agents",
		Aliases: []string{"scheduled-agent", "schedules", "cron"},
		Short:   "Cron-driven agents: run a prompt on a schedule",
		Long: `Manage agents that launch a session and run a prompt on a cron schedule.

Each agent pairs a 5-field UTC cron with a prompt, an optional org template,
and an optional Slack channel to post the result to. Enabling one creates the
schedule; disabling removes it.

Always validate with 'run-now' before enabling. It runs the same code path the
scheduled tick does — same template resolution, same Slack target — so a
misconfiguration surfaces immediately rather than at 11am in a shared channel.
Disabled agents can be run this way too, which is the recommended order:
create disabled, run-now, then enable.

Cron is 5 fields in UTC (minute hour day-of-month month day-of-week). 11am
Pacific is '0 18 * * *' in winter and '0 17 * * *' during daylight time --
there is no per-agent time zone, so pick the one that matches now and revisit
at the DST boundary.

Org-scoped: requires an org-scoped API key. Writes (create/update/delete/
run-now) need admin or owner role.

See https://docs.runtm.com/cloud-api/scheduled-agents for the full schemas.`,
	}
	cmd.AddCommand(
		newScheduledAgentList(rt),
		newScheduledAgentGet(rt),
		newScheduledAgentCreate(rt),
		newScheduledAgentUpdate(rt),
		newScheduledAgentRunNow(rt),
		newScheduledAgentDelete(rt),
	)
	return cmd
}

const scheduledAgentsPath = "/v1/scheduled-agents"

func scheduledAgentPath(id string) string {
	return scheduledAgentsPath + "/" + url.PathEscape(id)
}

// --- list / get -----------------------------------------------------------

func newScheduledAgentList(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "list",
		Short: "List scheduled agents (GET /api/v1/scheduled-agents)",
		Long: `Lists every scheduled agent in the org. Each entry carries 'next_run_at'
(the next UTC fire time, null when disabled) alongside 'last_run_at' and
'last_session_id', so you can tell at a glance whether a schedule is live and
when it last did anything.`,
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, "scheduled agents")
			if err != nil {
				return err
			}
			resp, err := c.Get(scheduledAgentsPath, nil)
			return runJSON(rt, resp, err)
		},
	}
}

func newScheduledAgentGet(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "get <agent_id>",
		Short: "Get one scheduled agent (GET /api/v1/scheduled-agents/{id})",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, "scheduled agents")
			if err != nil {
				return err
			}
			resp, err := c.Get(scheduledAgentPath(args[0]), nil)
			return runJSON(rt, resp, err)
		},
	}
}

// --- create / update ------------------------------------------------------

// scheduledAgentFlags is the shared flag set for create and update. Update
// only sends the flags that were explicitly passed, so a partial edit never
// clobbers fields it didn't mention.
type scheduledAgentFlags struct {
	name             string
	cron             string
	prompt           string
	template         string
	agent            string
	model            string
	enabled          bool
	disabled         bool
	slackIntegration string
	slackChannel     string
}

func (f *scheduledAgentFlags) register(cmd *cobra.Command) {
	cmd.Flags().StringVar(&f.name, "name", "", "Display name")
	cmd.Flags().StringVar(&f.cron, "cron", "", "5-field UTC cron, e.g. '0 18 * * 1' (Mondays 11am PT in winter)")
	cmd.Flags().StringVar(&f.prompt, "prompt", "", "Prompt run on every tick")
	cmd.Flags().StringVar(&f.template, "template", "", "Org template id the session launches from")
	cmd.Flags().StringVar(&f.agent, "agent", "", "Coding agent (claude-code, codex, …)")
	cmd.Flags().StringVar(&f.model, "model", "", "Model for the launched session")
	cmd.Flags().BoolVar(&f.enabled, "enabled", false, "Create/enable the schedule")
	cmd.Flags().BoolVar(&f.disabled, "disabled", false, "Turn the schedule off (keeps the agent)")
	cmd.Flags().StringVar(&f.slackIntegration, "slack-integration", "", "Slack integration id to post results as (requires --slack-channel)")
	cmd.Flags().StringVar(&f.slackChannel, "slack-channel", "", "Slack channel id to post results to (requires --slack-integration)")
}

// body collects the flags the user actually set. When onlyChanged is true
// (update) untouched flags are omitted entirely.
func (f *scheduledAgentFlags) body(cmd *cobra.Command, onlyChanged bool) (map[string]any, error) {
	if cmd.Flags().Changed("enabled") && cmd.Flags().Changed("disabled") {
		return nil, fmt.Errorf("--enabled and --disabled are mutually exclusive")
	}
	// The Slack integration and channel travel together; the API rejects one
	// without the other, so catch it here with a clearer message.
	if cmd.Flags().Changed("slack-integration") != cmd.Flags().Changed("slack-channel") {
		return nil, fmt.Errorf("--slack-integration and --slack-channel must be passed together")
	}

	body := map[string]any{}
	set := func(flag, key string, value any) {
		if !onlyChanged || cmd.Flags().Changed(flag) {
			body[key] = value
		}
	}
	set("name", "name", f.name)
	set("cron", "cron", f.cron)
	set("prompt", "prompt", f.prompt)

	if cmd.Flags().Changed("template") {
		body["org_template_id"] = f.template
	}
	if cmd.Flags().Changed("agent") {
		body["agent"] = f.agent
	}
	if cmd.Flags().Changed("model") {
		body["model"] = f.model
	}
	if cmd.Flags().Changed("enabled") {
		body["enabled"] = f.enabled
	}
	if cmd.Flags().Changed("disabled") {
		body["enabled"] = !f.disabled
	}
	if cmd.Flags().Changed("slack-integration") {
		body["slack_integration_id"] = f.slackIntegration
		body["slack_channel_id"] = f.slackChannel
	}
	return body, nil
}

func newScheduledAgentCreate(rt *Runtime) *cobra.Command {
	f := &scheduledAgentFlags{}
	cmd := &cobra.Command{
		Use:   "create",
		Short: "Create a scheduled agent (POST /api/v1/scheduled-agents)",
		Long: `Create a cron-driven agent. --name, --cron, and --prompt are required.

Agents are created enabled unless you pass --disabled. The safer order is to
create disabled, verify with 'run-now', then enable:

  runtm-api scheduled-agents create --name weekly-outbound --disabled \
    --cron '0 18 * * 1' --template <template_id> \
    --prompt 'Build this week's outbound lists and post them for approval'
  runtm-api scheduled-agents run-now <id>          # same path the tick takes
  runtm-api scheduled-agents update <id> --enabled

Pass --slack-integration and --slack-channel together to post each run's
result to a channel. Requires admin or owner role.`,
		RunE: func(cmd *cobra.Command, args []string) error {
			if f.name == "" || f.cron == "" || f.prompt == "" {
				return fmt.Errorf("--name, --cron, and --prompt are required")
			}
			body, err := f.body(cmd, false)
			if err != nil {
				return err
			}
			// Omit optional fields the user never set so the server defaults
			// apply (agent defaults to claude-code, model to the org default).
			for _, key := range []string{"org_template_id", "agent", "model"} {
				if v, ok := body[key]; ok && v == "" {
					delete(body, key)
				}
			}
			c, _, err := requireOrgClient(rt, "scheduled agents")
			if err != nil {
				return err
			}
			resp, err := c.PostJSON(scheduledAgentsPath, body)
			return runJSON(rt, resp, err)
		},
	}
	f.register(cmd)
	_ = cmd.MarkFlagRequired("name")
	_ = cmd.MarkFlagRequired("cron")
	_ = cmd.MarkFlagRequired("prompt")
	return cmd
}

func newScheduledAgentUpdate(rt *Runtime) *cobra.Command {
	f := &scheduledAgentFlags{}
	cmd := &cobra.Command{
		Use:   "update <agent_id>",
		Short: "Update a scheduled agent (PATCH /api/v1/scheduled-agents/{id})",
		Long: `Update a scheduled agent. Only the flags you pass are changed.

  runtm-api scheduled-agents update <id> --enabled
  runtm-api scheduled-agents update <id> --cron '0 17 * * 1'   # DST shift
  runtm-api scheduled-agents update <id> --disabled            # stop the schedule

Requires admin or owner role.`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			body, err := f.body(cmd, true)
			if err != nil {
				return err
			}
			if len(body) == 0 {
				return fmt.Errorf("pass at least one field to update (--name, --cron, --prompt, --template, --agent, --model, --enabled/--disabled, --slack-integration + --slack-channel)")
			}
			c, _, err := requireOrgClient(rt, "scheduled agents")
			if err != nil {
				return err
			}
			resp, err := c.PatchJSON(scheduledAgentPath(args[0]), body)
			return runJSON(rt, resp, err)
		},
	}
	f.register(cmd)
	return cmd
}

// --- run-now --------------------------------------------------------------

func newScheduledAgentRunNow(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:     "run-now <agent_id>",
		Aliases: []string{"run", "trigger"},
		Short:   "Fire one run immediately (POST /api/v1/scheduled-agents/{id}:run)",
		Long: `Run the agent now instead of waiting for the next tick.

This is the same code path Cloud Scheduler triggers — same template lookup,
same Slack target, same orchestrator call — so it is the way to prove a
schedule works before trusting it. Disabled agents run too, which is what
makes it useful for validating an agent before enabling it.

Returns the launched session_id. Follow the run with:
  runtm-api session get <session_id>
  runtm-api session history <session_id>

A run costs compute and can post to a shared channel, so this requires admin
or owner role.`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, "scheduled agents")
			if err != nil {
				return err
			}
			// Custom method (AIP-136): the colon suffix distinguishes an action
			// from the resource itself.
			resp, err := c.PostJSON(scheduledAgentPath(args[0])+":run", map[string]any{})
			return runJSON(rt, resp, err)
		},
	}
}

// --- delete ---------------------------------------------------------------

func newScheduledAgentDelete(rt *Runtime) *cobra.Command {
	var yes bool
	cmd := &cobra.Command{
		Use:   "delete <agent_id>",
		Short: "Delete a scheduled agent (DELETE /api/v1/scheduled-agents/{id})",
		Long: `Permanently delete the agent and its schedule. To stop a schedule while
keeping the agent, use 'update <id> --disabled' instead.`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if !yes {
				rt.WriteObject(map[string]any{
					"error": "Destructive operation requires --yes to confirm.",
					"hint":  "To stop the schedule without deleting, run `scheduled-agents update <id> --disabled`.",
				})
				return errSilent
			}
			c, _, err := requireOrgClient(rt, "scheduled agents")
			if err != nil {
				return err
			}
			resp, err := c.Delete(scheduledAgentPath(args[0]))
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().BoolVar(&yes, "yes", false, "Confirm deletion")
	return cmd
}
