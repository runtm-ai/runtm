package cmd

import (
	"encoding/json"
	"fmt"
	"html"
	"net/url"
	"os"
	"strconv"

	"github.com/runtm-ai/runtm/packages/agent/internal/client"
	"github.com/spf13/cobra"
)

// NewAgentsCommand returns `runtm-api agents`. Two related things live here:
//
//   - The agent ROSTER (no --type): named agents with an identity, system
//     instructions, session defaults, an evaluation rubric, and a budget.
//     This is the entity the dashboard's Agents page manages. Full CRUD over
//     /api/v1/agents, plus `scorecard` and `trigger-credentials`.
//
//   - Integration agents (--type slack|github|linear|email): the trigger
//     bindings that launch a coding session when an event arrives (a Slack
//     mention, a GitHub issue, a Linear assignment, an inbound email).
//
// Slack/GitHub/managed-Linear creation finishes in the browser (the platform
// install cannot be done headlessly), so `create` hands back a URL or page to
// open. Email and manual Linear (API key) provision headlessly.
func NewAgentsCommand(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:     "agents",
		Aliases: []string{"agent"},
		Short:   "Manage the agent roster and its Slack/GitHub/Linear/Email triggers",
		Long: `Manage named agents and the triggers that launch them.

Without --type, commands operate on the AGENT ROSTER: durable named agents
with a description, system instructions, session defaults (template + coding
agent), an evaluation rubric (evaluator_criteria), and a budget (economics).

  runtm-api agents list
  runtm-api agents create --name Donna --instructions 'Be terse.'
  runtm-api agents update <id> --evaluator-criteria '{"objective":"...","checks":["..."]}'
  runtm-api agents scorecard --days 30

With --type, commands operate on the trigger integrations that launch
sessions on events:

  Slack:  'create --type slack --name X' returns an authorize URL to open.
  GitHub: 'create --type github --name X' writes a page that submits the app
          manifest to GitHub for you to approve.
  Linear: 'create --type linear' returns an OAuth install URL, or pass
          --linear-api-key + --service-user for a headless manual bot.
  Email:  'create --type email --name X' provisions an inbox headlessly;
          inbound mail to it launches sessions.

Org-scoped: requires an org-scoped API key. Roster writes need the
integrations:write scope; trigger writes additionally need admin/owner role.`,
	}
	cmd.AddCommand(
		newAgentCreate(rt),
		newAgentList(rt),
		newAgentGet(rt),
		newAgentUpdate(rt),
		newAgentDelete(rt),
		newAgentTriggerCredentials(rt),
		newAgentScorecard(rt),
	)
	return cmd
}

// rosterPath is the agents-roster REST base (relative to /api/cloud).
const rosterPath = "/v1/agents"

func rosterItemPath(id string) string {
	return rosterPath + "/" + url.PathEscape(id)
}

// agentEndpoints captures the per-platform REST shape. List/get/update/delete
// manage EXISTING agents (plain API calls — no browser). Slack and GitHub use
// slightly different paths and update verbs.
type agentEndpoints struct {
	list       string
	item       func(id string) string
	updateVerb string // "PUT" (slack) or "POST" (github)
}

func agentEndpointsFor(agentType string) (agentEndpoints, error) {
	switch agentType {
	case "slack":
		return agentEndpoints{
			list:       "/v1/slack/integrations",
			item:       func(id string) string { return "/v1/slack/integration/" + url.PathEscape(id) },
			updateVerb: "PUT",
		}, nil
	case "github":
		return agentEndpoints{
			list:       "/v1/github/integrations",
			item:       func(id string) string { return "/v1/github/integrations/" + url.PathEscape(id) },
			updateVerb: "POST",
		}, nil
	case "linear":
		return agentEndpoints{
			list:       "/v1/linear/integrations",
			item:       func(id string) string { return "/v1/linear/integrations/" + url.PathEscape(id) },
			updateVerb: "POST",
		}, nil
	case "email":
		return agentEndpoints{
			list: "/v1/email/integrations",
			// Email has no GET-by-id route; `get` filters the list client-side.
			// Update/delete use the singular item path.
			item:       func(id string) string { return "/v1/email/integration/" + url.PathEscape(id) },
			updateVerb: "PUT",
		}, nil
	default:
		return agentEndpoints{}, fmt.Errorf("unsupported --type %q (expected slack, github, linear, or email; omit --type for the agent roster)", agentType)
	}
}

func newAgentList(rt *Runtime) *cobra.Command {
	var agentType string
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List roster agents (default) or trigger integrations (--type)",
		Long: `Without --type, lists the org's agent roster (GET /api/v1/agents): every
named agent with its defaults, evaluation rubric, and budget.

With --type slack|github|linear|email, lists that platform's trigger
integrations instead.`,
		RunE: func(cmd *cobra.Command, args []string) error {
			if agentType == "" {
				c, _, err := requireOrgClient(rt, "agents")
				if err != nil {
					return err
				}
				resp, err := c.Get(rosterPath, nil)
				return runJSON(rt, resp, err)
			}
			ep, err := agentEndpointsFor(agentType)
			if err != nil {
				return err
			}
			c, _, err := requireOrgClient(rt, agentType+" agents")
			if err != nil {
				return err
			}
			resp, err := c.Get(ep.list, nil)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&agentType, "type", "", "Trigger integration type: slack, github, linear, or email (omit for the roster)")
	return cmd
}

func newAgentGet(rt *Runtime) *cobra.Command {
	var agentType string
	cmd := &cobra.Command{
		Use:   "get <id>",
		Short: "Get one roster agent (default) or trigger integration (--type)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if agentType == "" {
				c, _, err := requireOrgClient(rt, "agents")
				if err != nil {
					return err
				}
				resp, err := c.Get(rosterItemPath(args[0]), nil)
				return runJSON(rt, resp, err)
			}
			ep, err := agentEndpointsFor(agentType)
			if err != nil {
				return err
			}
			c, _, err := requireOrgClient(rt, agentType+" agents")
			if err != nil {
				return err
			}
			// Email exposes no GET-by-id route; resolve from the list.
			if agentType == "email" {
				return emailIntegrationByID(rt, c, ep.list, args[0])
			}
			resp, err := c.Get(ep.item(args[0]), nil)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&agentType, "type", "", "Trigger integration type: slack, github, linear, or email (omit for the roster)")
	return cmd
}

// emailIntegrationByID lists email integrations and emits the one matching id.
func emailIntegrationByID(rt *Runtime, c *client.Client, listPath, id string) error {
	raw, err := c.Get(listPath, nil)
	if err != nil {
		return err
	}
	var out struct {
		Integrations []json.RawMessage `json:"integrations"`
	}
	if jerr := json.Unmarshal(raw, &out); jerr != nil {
		return fmt.Errorf("could not parse email integrations list: %w", jerr)
	}
	for _, item := range out.Integrations {
		var probe struct {
			ID string `json:"id"`
		}
		if json.Unmarshal(item, &probe) == nil && probe.ID == id {
			rt.WriteJSON(item)
			return nil
		}
	}
	return fmt.Errorf("email integration %s not found (run `runtm-api agents list --type email`)", id)
}

// rosterOnlyFlags and integrationOnlyFlags gate the shared update/create flag
// set: the roster and the trigger integrations accept different fields, and a
// silent mismatch would drop the edit on the floor.
var rosterOnlyFlags = []string{
	"description", "instructions", "avatar", "evaluator-criteria", "economics", "clear-template",
}
var integrationOnlyFlags = []string{
	"github-repo", "rate-limit", "service-user", "model", "config", "enabled", "disabled",
}

func checkFlagsForMode(cmd *cobra.Command, agentType string) error {
	if agentType == "" {
		for _, f := range integrationOnlyFlags {
			if cmd.Flags().Changed(f) {
				return fmt.Errorf("--%s only applies to trigger integrations; pass --type slack|github|linear|email", f)
			}
		}
		return nil
	}
	for _, f := range rosterOnlyFlags {
		if cmd.Flags().Changed(f) {
			return fmt.Errorf("--%s only applies to roster agents; drop --type to edit the roster", f)
		}
	}
	return nil
}

// rosterBody collects the roster fields the user actually set.
func rosterBody(cmd *cobra.Command, name, description, instructions, avatarJSON, template, agent, criteriaJSON, economicsJSON string) (map[string]any, error) {
	body := map[string]any{}
	if cmd.Flags().Changed("name") {
		body["name"] = name
	}
	if cmd.Flags().Changed("description") {
		body["description"] = description
	}
	if cmd.Flags().Changed("instructions") {
		body["system_instructions"] = instructions
	}
	if cmd.Flags().Changed("avatar") {
		avatar, err := parseJSONObject(avatarJSON)
		if err != nil {
			return nil, fmt.Errorf("--avatar: %w", err)
		}
		body["avatar"] = avatar
	}
	if cmd.Flags().Changed("template") {
		body["default_template"] = template
	}
	if cmd.Flags().Changed("clear-template") {
		// Explicit null clears the default and fans out to linked triggers.
		body["default_template"] = nil
	}
	if cmd.Flags().Changed("agent") {
		body["default_agent"] = agent
	}
	if cmd.Flags().Changed("evaluator-criteria") {
		criteria, err := parseJSONObject(criteriaJSON)
		if err != nil {
			return nil, fmt.Errorf("--evaluator-criteria: %w", err)
		}
		body["evaluator_criteria"] = criteria
	}
	if cmd.Flags().Changed("economics") {
		economics, err := parseJSONObject(economicsJSON)
		if err != nil {
			return nil, fmt.Errorf("--economics: %w", err)
		}
		body["economics"] = economics
	}
	return body, nil
}

func newAgentUpdate(rt *Runtime) *cobra.Command {
	var (
		agentType    string
		name         string
		description  string
		instructions string
		avatarJSON   string
		criteriaJSON string
		econJSON     string
		agent        string
		template     string
		githubRepo   string
		rateLimit    int
		serviceUser  string
		enabled      bool
		model        string
		configJSON   string
	)
	cmd := &cobra.Command{
		Use:   "update <id>",
		Short: "Edit a roster agent (default) or a trigger integration (--type)",
		Long: `Update an agent. Only the flags you pass are changed.

Without --type, edits the roster agent: identity, instructions, session
defaults, evaluation rubric, and budget. Template/agent edits fan out to
every linked trigger.

  runtm-api agents update <id> --instructions 'Be terse. Prefer PRs.'
  runtm-api agents update <id> --template gtm-machine --agent codex
  runtm-api agents update <id> --clear-template
  runtm-api agents update <id> --evaluator-criteria '{"objective":"Resolve the ticket","checks":["opened a PR"]}'
  runtm-api agents update <id> --economics '{"budget":{"monthly_usd_cap":50}}'

With --type, edits the trigger integration:

  runtm-api agents update <id> --type slack --template my-tmpl --agent codex
  runtm-api agents update <id> --type github --model opus --rate-limit 20
  runtm-api agents update <id> --type linear --disabled
  runtm-api agents update <id> --type email --name support-inbox`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if err := checkFlagsForMode(cmd, agentType); err != nil {
				return err
			}

			// Roster edit (no --type).
			if agentType == "" {
				body, err := rosterBody(cmd, name, description, instructions, avatarJSON, template, agent, criteriaJSON, econJSON)
				if err != nil {
					return err
				}
				if len(body) == 0 {
					return fmt.Errorf("pass at least one field to update (--name, --description, --instructions, --avatar, --template/--clear-template, --agent, --evaluator-criteria, --economics)")
				}
				c, _, err := requireOrgClient(rt, "agents")
				if err != nil {
					return err
				}
				resp, err := c.PatchJSON(rosterItemPath(args[0]), body)
				return runJSON(rt, resp, err)
			}

			// Trigger integration edit (--type).
			ep, err := agentEndpointsFor(agentType)
			if err != nil {
				return err
			}
			body := map[string]any{}
			if cmd.Flags().Changed("name") {
				body["name"] = name
			}
			if cmd.Flags().Changed("agent") {
				body["default_agent"] = agent
			}
			if cmd.Flags().Changed("template") {
				body["default_template"] = template
			}
			if cmd.Flags().Changed("github-repo") {
				body["default_github_repo"] = githubRepo
			}
			if cmd.Flags().Changed("rate-limit") {
				body["rate_limit_per_hour"] = rateLimit
			}
			if cmd.Flags().Changed("service-user") {
				body["service_user_id"] = serviceUser
			}
			if cmd.Flags().Changed("enabled") {
				body["enabled"] = enabled
			}
			if cmd.Flags().Changed("disabled") {
				body["enabled"] = false
			}

			// config is JSON-patch merged on the backend, so we only send the
			// keys being changed. --model is shorthand for config.default_model;
			// --config is the escape hatch for any other key (triggers,
			// system_instructions, *_template_map, event_context_fields, ...).
			config := map[string]any{}
			if cmd.Flags().Changed("config") {
				parsed, perr := parseJSONObject(configJSON)
				if perr != nil {
					return fmt.Errorf("--config: %w", perr)
				}
				config = parsed
			}
			if cmd.Flags().Changed("model") {
				config["default_model"] = model
			}
			if len(config) > 0 {
				body["config"] = config
			}

			if len(body) == 0 {
				return fmt.Errorf("pass at least one field to update (e.g. --template, --agent, --model, --config, --enabled/--disabled)")
			}

			c, _, err := requireOrgClient(rt, agentType+" agents")
			if err != nil {
				return err
			}
			var resp []byte
			if ep.updateVerb == "PUT" {
				resp, err = c.PutJSON(ep.item(args[0]), body)
			} else {
				resp, err = c.PostJSON(ep.item(args[0]), body)
			}
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&agentType, "type", "", "Trigger integration type: slack, github, linear, or email (omit for the roster)")
	cmd.Flags().StringVar(&name, "name", "", "Display name")
	cmd.Flags().StringVar(&description, "description", "", "Roster: short description")
	cmd.Flags().StringVar(&instructions, "instructions", "", "Roster: system instructions injected into every session")
	cmd.Flags().StringVar(&avatarJSON, "avatar", "", `Roster: avatar object as JSON, e.g. '{"kind":"icon","icon":"bot","color":"blue"}'`)
	cmd.Flags().StringVar(&criteriaJSON, "evaluator-criteria", "", `Roster: evaluation rubric as JSON, e.g. '{"objective":"...","checks":["..."]}' (version bumps automatically)`)
	cmd.Flags().StringVar(&econJSON, "economics", "", `Roster: task values + budget as JSON, e.g. '{"tasks":{"triage":{"value_usd":10,"minutes":20}},"budget":{"monthly_usd_cap":50}}'`)
	cmd.Flags().Bool("clear-template", false, "Roster: clear the default template (fans out to linked triggers)")
	cmd.Flags().StringVar(&agent, "agent", "", "Default coding agent to launch (claude-code, codex, ...)")
	cmd.Flags().StringVar(&template, "template", "", "Default org template for launched sessions")
	cmd.Flags().StringVar(&githubRepo, "github-repo", "", "Integrations: default GitHub repo (owner/repo)")
	cmd.Flags().IntVar(&rateLimit, "rate-limit", 0, "Integrations: max events handled per hour (1-100)")
	cmd.Flags().StringVar(&serviceUser, "service-user", "", "Integrations: runtime user id sessions run as")
	cmd.Flags().BoolVar(&enabled, "enabled", false, "Integrations: enable (use --disabled to turn off)")
	cmd.Flags().Bool("disabled", false, "Integrations: disable")
	cmd.Flags().StringVar(&model, "model", "", "Integrations: default model (sets config.default_model)")
	cmd.Flags().StringVar(&configJSON, "config", "", "Integrations: merge raw JSON into config (triggers, system_instructions, *_template_map, ...)")
	return cmd
}

func newAgentDelete(rt *Runtime) *cobra.Command {
	var (
		agentType string
		yes       bool
	)
	cmd := &cobra.Command{
		Use:   "delete <id>",
		Short: "Delete a roster agent (default) or a trigger integration (--type)",
		Long: `Delete an agent. Without --type this removes the roster row; note the row
is recreated by the lazy sync while any trigger integration still references
it, so delete the triggers first (the order the dashboard enforces).`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if !yes {
				rt.WriteObject(map[string]any{
					"error": "Destructive operation requires --yes to confirm.",
					"hint":  "Pass --yes when you are sure.",
				})
				return errSilent
			}
			if agentType == "" {
				c, _, err := requireOrgClient(rt, "agents")
				if err != nil {
					return err
				}
				resp, err := c.Delete(rosterItemPath(args[0]))
				return runJSON(rt, resp, err)
			}
			ep, err := agentEndpointsFor(agentType)
			if err != nil {
				return err
			}
			c, _, err := requireOrgClient(rt, agentType+" agents")
			if err != nil {
				return err
			}
			resp, err := c.Delete(ep.item(args[0]))
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&agentType, "type", "", "Trigger integration type: slack, github, linear, or email (omit for the roster)")
	cmd.Flags().BoolVar(&yes, "yes", false, "Confirm deletion")
	return cmd
}

func newAgentTriggerCredentials(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "trigger-credentials",
		Short: "List referencable agent trigger tokens (GET /api/v1/agents/trigger-credentials)",
		Long: `Lists the trigger credentials each roster agent exposes (metadata only,
never the secret). The 'ref' values are what a connection stores as a
credential placeholder; they are dereferenced at session boot.`,
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, "agents")
			if err != nil {
				return err
			}
			resp, err := c.Get(rosterPath+"/trigger-credentials", nil)
			return runJSON(rt, resp, err)
		},
	}
}

func newAgentScorecard(rt *Runtime) *cobra.Command {
	var (
		days  int
		start string
		end   string
	)
	cmd := &cobra.Command{
		Use:   "scorecard",
		Short: "Per-agent performance and spend (GET /api/sessions/telemetry/agents)",
		Long: `The per-agent scorecard: sessions, turns, token spend, graded runs,
objective hit rate, value returned, and budget state for every roster agent.
This is the read side of the evaluation loop; set the rubric with
'agents update <id> --evaluator-criteria' and read one run's verdict with
'session grade <session_id>'.`,
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, "agents")
			if err != nil {
				return err
			}
			q := url.Values{}
			if cmd.Flags().Changed("days") {
				q.Set("days", strconv.Itoa(days))
			}
			if start != "" {
				q.Set("start", start)
			}
			if end != "" {
				q.Set("end", end)
			}
			resp, err := c.Get("/sessions/telemetry/agents", q)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().IntVar(&days, "days", 30, "Rolling window in days (max 365)")
	cmd.Flags().StringVar(&start, "start", "", "Window start (ISO date/datetime; overrides --days with --end)")
	cmd.Flags().StringVar(&end, "end", "", "Window end (ISO date/datetime)")
	return cmd
}

func newAgentCreate(rt *Runtime) *cobra.Command {
	var (
		agentType    string
		name         string
		description  string
		instructions string
		avatarJSON   string
		criteriaJSON string
		econJSON     string
		agent        string
		template     string
		githubRepo   string
		rateLimit    int
		githubOrg    string
		returnURL    string
		linearAPIKey string
		serviceUser  string
		agentID      string
	)
	cmd := &cobra.Command{
		Use:   "create",
		Short: "Create a roster agent (default) or a trigger integration (--type)",
		Long: `Create an agent.

Without --type, creates a roster agent headlessly: a named agent with
identity, instructions, session defaults, rubric, and budget. Bind triggers
to it afterwards.

  runtm-api agents create --name Donna --instructions 'Be terse.' \
    --template gtm-machine --evaluator-criteria '{"objective":"...","checks":["..."]}'

With --type, creates a trigger integration. Slack, GitHub, and managed
Linear finish in a browser; email and manual Linear are headless:

  runtm-api agents create --type slack --name George
  runtm-api agents create --type github --name "Runtime Bot"
  runtm-api agents create --type linear                       # OAuth install URL
  runtm-api agents create --type linear --linear-api-key lin_api_... --service-user <user_id>
  runtm-api agents create --type email --name support-inbox --agent-id <roster_agent_id>`,
		RunE: func(cmd *cobra.Command, args []string) error {
			// Roster create (no --type).
			if agentType == "" {
				if name == "" {
					return fmt.Errorf("--name is required")
				}
				body, err := rosterBody(cmd, name, description, instructions, avatarJSON, template, agent, criteriaJSON, econJSON)
				if err != nil {
					return err
				}
				c, _, err := requireOrgClient(rt, "agents")
				if err != nil {
					return err
				}
				resp, err := c.PostJSON(rosterPath, body)
				return runJSON(rt, resp, err)
			}

			for _, f := range []string{"description", "instructions", "avatar", "evaluator-criteria", "economics"} {
				if cmd.Flags().Changed(f) {
					return fmt.Errorf("--%s only applies to roster agents; drop --type (bind the trigger to a roster agent afterwards)", f)
				}
			}
			switch agentType {
			case "slack":
				if name == "" {
					return fmt.Errorf("--name is required")
				}
				return createSlackAgent(rt, name, agent, template, githubRepo, rateLimit)
			case "github":
				if name == "" {
					return fmt.Errorf("--name is required")
				}
				return createGitHubAgent(rt, name, githubOrg, returnURL)
			case "linear":
				return createLinearAgent(rt, name, linearAPIKey, serviceUser, agent, template, githubRepo, rateLimit)
			case "email":
				if name == "" {
					return fmt.Errorf("--name is required")
				}
				return createEmailAgent(rt, name, agentID, agent, template, githubRepo)
			default:
				return fmt.Errorf("unsupported --type %q (expected slack, github, linear, or email)", agentType)
			}
		},
	}
	cmd.Flags().StringVar(&agentType, "type", "", "Trigger integration type: slack, github, linear, or email (omit for a roster agent)")
	cmd.Flags().StringVar(&name, "name", "", "Display name for the agent / app")
	cmd.Flags().StringVar(&description, "description", "", "Roster: short description")
	cmd.Flags().StringVar(&instructions, "instructions", "", "Roster: system instructions injected into every session")
	cmd.Flags().StringVar(&avatarJSON, "avatar", "", `Roster: avatar object as JSON, e.g. '{"kind":"icon","icon":"bot","color":"blue"}'`)
	cmd.Flags().StringVar(&criteriaJSON, "evaluator-criteria", "", `Roster: evaluation rubric as JSON ('{"objective":"...","checks":["..."]}')`)
	cmd.Flags().StringVar(&econJSON, "economics", "", "Roster: task values + budget as JSON")
	cmd.Flags().StringVar(&agent, "agent", "", "Coding agent to launch (claude-code, codex, ...)")
	cmd.Flags().StringVar(&template, "template", "", "Default org template for launched sessions")
	cmd.Flags().StringVar(&githubRepo, "github-repo", "", "Default GitHub repo (owner/repo) for launched sessions")
	cmd.Flags().IntVar(&rateLimit, "rate-limit", 0, "Integrations: max events handled per hour (1-100)")
	cmd.Flags().StringVar(&githubOrg, "github-org", "", "github: create the app under this GitHub org (default: your personal account)")
	cmd.Flags().StringVar(&returnURL, "return-url", "", "github: URL to return to after the app is created")
	cmd.Flags().StringVar(&linearAPIKey, "linear-api-key", "", "linear: create a headless manual bot from a Linear personal API key (requires --service-user)")
	cmd.Flags().StringVar(&serviceUser, "service-user", "", "linear: runtime user id the bot's sessions run as (required with --linear-api-key)")
	cmd.Flags().StringVar(&agentID, "agent-id", "", "email: roster agent id the inbox binds to (stored in config.agent_id)")
	return cmd
}

// createLinearAgent provisions a Linear trigger. With an API key it creates a
// manual bot headlessly; otherwise it returns the managed OAuth install URL to
// open in a browser.
func createLinearAgent(rt *Runtime, name, apiKey, serviceUser, agent, template, githubRepo string, rateLimit int) error {
	c, _, err := requireOrgClient(rt, "Linear agents")
	if err != nil {
		return err
	}

	if apiKey != "" {
		if serviceUser == "" {
			return fmt.Errorf("--service-user is required with --linear-api-key (the runtime user id the bot's sessions run as)")
		}
		body := map[string]any{
			"api_key":         apiKey,
			"service_user_id": serviceUser,
		}
		if name != "" {
			body["name"] = name
		}
		if agent != "" {
			body["default_agent"] = agent
		}
		if template != "" {
			body["default_template"] = template
		}
		if githubRepo != "" {
			body["default_github_repo"] = githubRepo
		}
		if rateLimit > 0 {
			body["rate_limit_per_hour"] = rateLimit
		}
		resp, err := c.PostJSON("/v1/linear/integrations/manual", body)
		return runJSON(rt, resp, err)
	}

	resp, err := c.Get("/v1/linear/oauth/install-url", nil)
	if err != nil {
		return runJSON(rt, resp, err)
	}
	var out struct {
		URL string `json:"url"`
	}
	if jerr := json.Unmarshal(resp, &out); jerr != nil || out.URL == "" {
		return runJSON(rt, resp, nil)
	}
	rt.WriteObject(map[string]any{
		"type":        "linear",
		"install_url": out.URL,
		"next_step": "Open install_url in a browser and authorize the app for your " +
			"Linear workspace. The integration is created once you approve. For a " +
			"fully headless setup, rerun with --linear-api-key and --service-user instead.",
	})
	return nil
}

// createEmailAgent provisions an AgentMail inbox headlessly. Inbound mail to
// the returned address launches or follows up sessions.
func createEmailAgent(rt *Runtime, name, agentID, agent, template, githubRepo string) error {
	c, _, err := requireOrgClient(rt, "email agents")
	if err != nil {
		return err
	}
	body := map[string]any{"name": name}
	if agentID != "" {
		body["agent_id"] = agentID
	}
	if agent != "" {
		body["default_agent"] = agent
	}
	if template != "" {
		body["default_template"] = template
	}
	if githubRepo != "" {
		body["default_github_repo"] = githubRepo
	}
	resp, err := c.PostJSON("/v1/email/integration", body)
	return runJSON(rt, resp, err)
}

// createSlackAgent provisions a managed Slack app and returns its authorize URL.
// Requires the org's Slack config token to already be set (dashboard, one-time).
func createSlackAgent(rt *Runtime, name, agent, template, githubRepo string, rateLimit int) error {
	c, _, err := requireOrgClient(rt, "Slack agents")
	if err != nil {
		return err
	}
	body := map[string]any{"app_name": name}
	if agent != "" {
		body["default_agent"] = agent
	}
	if template != "" {
		body["default_template"] = template
	}
	if githubRepo != "" {
		body["default_github_repo"] = githubRepo
	}
	if rateLimit > 0 {
		body["rate_limit_per_hour"] = rateLimit
	}

	resp, err := c.PostJSON("/v1/slack/managed-app", body)
	if err != nil {
		return runJSON(rt, resp, err)
	}
	var out struct {
		AuthorizeURL string `json:"authorize_url"`
		APIAppID     string `json:"api_app_id"`
	}
	if jerr := json.Unmarshal(resp, &out); jerr != nil || out.AuthorizeURL == "" {
		// Fall back to the raw response so nothing is hidden.
		return runJSON(rt, resp, nil)
	}
	rt.WriteObject(map[string]any{
		"type":          "slack",
		"name":          name,
		"api_app_id":    out.APIAppID,
		"authorize_url": out.AuthorizeURL,
		"next_step": "Open authorize_url in a browser and approve the app for your " +
			"Slack workspace. The agent is created once you approve. " +
			"(If this errors with a missing config token, set the org's Slack app " +
			"configuration token in the dashboard first.)",
	})
	return nil
}

// createGitHubAgent fetches the GitHub App manifest and writes a self-submitting
// HTML page. GitHub's App-manifest flow requires a real form POST of the
// manifest, so a plain link won't work — opening this page submits it.
func createGitHubAgent(rt *Runtime, name, githubOrg, returnURL string) error {
	c, _, err := requireOrgClient(rt, "GitHub agents")
	if err != nil {
		return err
	}
	q := url.Values{}
	q.Set("app_name", name)
	if githubOrg != "" {
		q.Set("github_org", githubOrg)
	}
	if returnURL != "" {
		q.Set("return_url", returnURL)
	}

	resp, err := c.Get("/v1/github/manifest", q)
	if err != nil {
		return runJSON(rt, resp, err)
	}
	var out struct {
		CreateURL string          `json:"create_url"`
		Manifest  json.RawMessage `json:"manifest"`
		State     string          `json:"state"`
	}
	if jerr := json.Unmarshal(resp, &out); jerr != nil || out.CreateURL == "" || len(out.Manifest) == 0 {
		return runJSON(rt, resp, nil)
	}

	path, werr := writeGitHubManifestPage(name, out.CreateURL, out.Manifest)
	if werr != nil {
		// Still hand back the pieces so the user can submit manually.
		rt.WriteObject(map[string]any{
			"type":       "github",
			"name":       name,
			"create_url": out.CreateURL,
			"manifest":   json.RawMessage(out.Manifest),
			"error":      fmt.Sprintf("could not write the submit page: %v", werr),
			"next_step":  "POST the manifest field to create_url to finish creating the app.",
		})
		return nil
	}
	rt.WriteObject(map[string]any{
		"type":       "github",
		"name":       name,
		"create_url": out.CreateURL,
		"open":       path,
		"next_step": "Open the file at 'open' in a browser. GitHub requires the app " +
			"manifest to be submitted as a form POST, so the page does that for you; " +
			"approve the app on GitHub to finish creating the agent.",
	})
	return nil
}

// writeGitHubManifestPage writes a minimal auto-submitting form to a temp file
// and returns its path. Opening it POSTs the manifest to GitHub.
func writeGitHubManifestPage(name, createURL string, manifest json.RawMessage) (string, error) {
	f, err := os.CreateTemp("", "runtm-github-app-*.html")
	if err != nil {
		return "", err
	}
	defer f.Close()
	page := fmt.Sprintf(`<!doctype html>
<meta charset="utf-8">
<title>Create GitHub App: %[1]s</title>
<body onload="document.forms[0].submit()">
  <p>Submitting the GitHub App manifest for "%[1]s"… if nothing happens, click the button.</p>
  <form method="post" action="%[2]s">
    <input type="hidden" name="manifest" value="%[3]s">
    <button type="submit">Create GitHub App on GitHub</button>
  </form>
</body>`,
		html.EscapeString(name),
		html.EscapeString(createURL),
		html.EscapeString(string(manifest)),
	)
	if _, err := f.WriteString(page); err != nil {
		return "", err
	}
	return f.Name(), nil
}
