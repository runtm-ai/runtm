package cmd

import (
	"encoding/json"
	"fmt"
	"html"
	"net/url"
	"os"

	"github.com/spf13/cobra"
)

// NewAgentsCommand returns `runtm-api agents` — create integration agents
// (Slack / GitHub) that launch a coding agent when an event arrives.
//
// Creation finishes in the browser: the platform's install/authorize step
// can't be done headlessly. So `create` returns something to open —
//   - Slack: an authorize URL (click it, approve in your workspace)
//   - GitHub: a local HTML page that form-POSTs the app manifest to GitHub
//     (GitHub requires a real form POST, not a plain link)
//
// Linear is intentionally not supported here yet — use the dashboard.
func NewAgentsCommand(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "agents",
		Short: "Create integration agents (Slack, GitHub) that respond to events",
		Long: `Create an agent that launches a coding session when something happens on a
connected platform — a Slack mention, a GitHub issue/PR.

The final install step happens in your browser (you authorize the app into your
workspace/account), so 'create' hands back a URL or page to open. Org-scoped:
requires an org-scoped API key with admin/owner role and the integrations:write
scope (--org cannot substitute for one).

  Slack:  needs the org's Slack app config token set once in the dashboard,
          then 'create --type slack --name X' returns an authorize URL.
  GitHub: 'create --type github --name X' writes a page that submits the app
          manifest to GitHub for you to approve.`,
	}
	cmd.AddCommand(
		newAgentCreate(rt),
		newAgentList(rt),
		newAgentGet(rt),
		newAgentUpdate(rt),
		newAgentDelete(rt),
	)
	return cmd
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
		return agentEndpoints{}, fmt.Errorf("linear agents aren't supported from the CLI yet — manage them in the dashboard")
	case "":
		return agentEndpoints{}, fmt.Errorf("--type is required (slack or github)")
	default:
		return agentEndpoints{}, fmt.Errorf("unsupported --type %q (expected slack or github)", agentType)
	}
}

func newAgentList(rt *Runtime) *cobra.Command {
	var agentType string
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List Slack or GitHub agents in the org",
		RunE: func(cmd *cobra.Command, args []string) error {
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
	cmd.Flags().StringVar(&agentType, "type", "", "Agent type: slack or github (required)")
	_ = cmd.MarkFlagRequired("type")
	return cmd
}

func newAgentGet(rt *Runtime) *cobra.Command {
	var agentType string
	cmd := &cobra.Command{
		Use:   "get <id>",
		Short: "Get one Slack or GitHub agent by id",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			ep, err := agentEndpointsFor(agentType)
			if err != nil {
				return err
			}
			c, _, err := requireOrgClient(rt, agentType+" agents")
			if err != nil {
				return err
			}
			resp, err := c.Get(ep.item(args[0]), nil)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&agentType, "type", "", "Agent type: slack or github (required)")
	_ = cmd.MarkFlagRequired("type")
	return cmd
}

func newAgentUpdate(rt *Runtime) *cobra.Command {
	var (
		agentType   string
		name        string
		agent       string
		template    string
		githubRepo  string
		rateLimit   int
		serviceUser string
		enabled     bool
		model       string
		configJSON  string
	)
	cmd := &cobra.Command{
		Use:   "update <id>",
		Short: "Edit an agent's defaults (template, agent, repo, rate limit, model, …)",
		Long: `Update an existing Slack or GitHub agent. Only the flags you pass are
changed (everything else is left as-is). The agent's coding behaviour and
defaults live here.

Examples:
  runtm-api agents update <id> --type slack --template my-tmpl --agent codex
  runtm-api agents update <id> --type github --model opus --rate-limit 20
  runtm-api agents update <id> --type slack --disabled
  runtm-api agents update <id> --type slack --config '{"system_instructions":"Be concise","triggers":{"new_dm_message":true}}'`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
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
			// system_instructions, *_template_map, event_context_fields, …).
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
	cmd.Flags().StringVar(&agentType, "type", "", "Agent type: slack or github (required)")
	cmd.Flags().StringVar(&name, "name", "", "Display name")
	cmd.Flags().StringVar(&agent, "agent", "", "Default coding agent to launch (claude-code, codex, …)")
	cmd.Flags().StringVar(&template, "template", "", "Default org template id for launched sessions")
	cmd.Flags().StringVar(&githubRepo, "github-repo", "", "Default GitHub repo (owner/repo)")
	cmd.Flags().IntVar(&rateLimit, "rate-limit", 0, "Max events handled per hour (1-100)")
	cmd.Flags().StringVar(&serviceUser, "service-user", "", "Runtime user id sessions run as")
	cmd.Flags().BoolVar(&enabled, "enabled", false, "Enable the agent (use --disabled to turn off)")
	cmd.Flags().Bool("disabled", false, "Disable the agent")
	cmd.Flags().StringVar(&model, "model", "", "Default model for launched sessions (sets config.default_model)")
	cmd.Flags().StringVar(&configJSON, "config", "", "Merge raw JSON into the agent's config (triggers, system_instructions, *_template_map, …)")
	_ = cmd.MarkFlagRequired("type")
	return cmd
}

func newAgentDelete(rt *Runtime) *cobra.Command {
	var (
		agentType string
		yes       bool
	)
	cmd := &cobra.Command{
		Use:   "delete <id>",
		Short: "Delete a Slack or GitHub agent",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			ep, err := agentEndpointsFor(agentType)
			if err != nil {
				return err
			}
			if !yes {
				rt.WriteObject(map[string]any{
					"error": "Destructive operation requires --yes to confirm.",
					"hint":  "Pass --yes when you are sure.",
				})
				return errSilent
			}
			c, _, err := requireOrgClient(rt, agentType+" agents")
			if err != nil {
				return err
			}
			resp, err := c.Delete(ep.item(args[0]))
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&agentType, "type", "", "Agent type: slack or github (required)")
	cmd.Flags().BoolVar(&yes, "yes", false, "Confirm deletion")
	_ = cmd.MarkFlagRequired("type")
	return cmd
}

func newAgentCreate(rt *Runtime) *cobra.Command {
	var (
		agentType  string
		name       string
		agent      string
		template   string
		githubRepo string
		rateLimit  int
		githubOrg  string
		returnURL  string
	)
	cmd := &cobra.Command{
		Use:   "create",
		Short: "Create a Slack or GitHub agent (returns a URL/page to authorize)",
		Long: `Create an integration agent. The platform install is finished in a browser.

Examples:
  runtm-api agents create --type slack --name George
  runtm-api agents create --type github --name "Runtime Bot"`,
		RunE: func(cmd *cobra.Command, args []string) error {
			if name == "" {
				return fmt.Errorf("--name is required")
			}
			switch agentType {
			case "slack":
				return createSlackAgent(rt, name, agent, template, githubRepo, rateLimit)
			case "github":
				return createGitHubAgent(rt, name, githubOrg, returnURL)
			case "linear":
				return fmt.Errorf("linear agents aren't supported from the CLI yet — create them in the dashboard")
			case "":
				return fmt.Errorf("--type is required (slack or github)")
			default:
				return fmt.Errorf("unsupported --type %q (expected slack or github)", agentType)
			}
		},
	}
	cmd.Flags().StringVar(&agentType, "type", "", "Agent type: slack or github (required)")
	cmd.Flags().StringVar(&name, "name", "", "Display name for the agent / app (required)")
	cmd.Flags().StringVar(&agent, "agent", "", "Coding agent to launch on events (default: server default, claude-code)")
	cmd.Flags().StringVar(&template, "template", "", "Default org template id for launched sessions")
	cmd.Flags().StringVar(&githubRepo, "github-repo", "", "Default GitHub repo (owner/repo) for launched sessions")
	cmd.Flags().IntVar(&rateLimit, "rate-limit", 0, "slack: max events handled per hour (1-100; default 30)")
	cmd.Flags().StringVar(&githubOrg, "github-org", "", "github: create the app under this GitHub org (default: your personal account)")
	cmd.Flags().StringVar(&returnURL, "return-url", "", "github: URL to return to after the app is created")
	_ = cmd.MarkFlagRequired("type")
	_ = cmd.MarkFlagRequired("name")
	return cmd
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
