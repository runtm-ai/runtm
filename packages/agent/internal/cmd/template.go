package cmd

import (
	"encoding/json"
	"fmt"
	"net/url"
	"strconv"
	"strings"

	"github.com/runtm-ai/runtm/packages/agent/internal/client"
	"github.com/spf13/cobra"
)

// sessionArgInput is the JSON form accepted by --session-arg for full control
// over a session argument (matches the backend SessionArgSchema). Pointers
// distinguish "absent" from "explicit zero value" so defaults apply correctly.
type sessionArgInput struct {
	Key      string   `json:"key"`
	Label    string   `json:"label"`
	Type     string   `json:"type"`
	Required *bool    `json:"required"`
	Default  *string  `json:"default"`
	Options  []string `json:"options"`
	HelpText *string  `json:"help_text"`
}

// buildSessionArg normalizes and validates one session argument into the map
// shape the PATCH /api/org-templates/{id} endpoint expects.
func buildSessionArg(key, label, typ string, required bool, def any, options []string, helpText any) (map[string]any, error) {
	key = strings.TrimSpace(key)
	if key == "" {
		return nil, fmt.Errorf("session arg: key must not be empty")
	}
	if typ == "" {
		typ = "text"
	}
	switch typ {
	case "text", "select", "boolean":
	default:
		return nil, fmt.Errorf("session arg %q: type must be text, select, or boolean (got %q)", key, typ)
	}
	if label == "" {
		label = key
	}
	if options == nil {
		options = []string{}
	}
	if typ == "select" && len(options) == 0 {
		return nil, fmt.Errorf("session arg %q: select type requires non-empty options", key)
	}
	return map[string]any{
		"key":       key,
		"label":     label,
		"type":      typ,
		"required":  required,
		"default":   def,
		"options":   options,
		"help_text": helpText,
	}, nil
}

// parseSessionArgs converts repeatable --session-arg specs into the session_args
// payload. Each becomes an argument collected when a member launches a session
// from the template and injected into the sandbox as an environment variable.
//
// Two forms are accepted per spec:
//
//	Shorthand (text args):
//	  "KEY=DEFAULT"  -> optional text arg, defaulting to DEFAULT
//	  "KEY"          -> required text arg, no default
//
//	JSON (full control over type/options/label/help_text/required):
//	  '{"key":"ENV","type":"select","options":["dev","prod"],"default":"dev","required":true,"label":"Environment","help_text":"Target env"}'
//	  '{"key":"VERBOSE","type":"boolean","default":"false"}'
func parseSessionArgs(specs []string) ([]map[string]any, error) {
	out := make([]map[string]any, 0, len(specs))
	for _, spec := range specs {
		s := strings.TrimSpace(spec)

		if strings.HasPrefix(s, "{") {
			var in sessionArgInput
			if err := json.Unmarshal([]byte(s), &in); err != nil {
				return nil, fmt.Errorf("invalid --session-arg JSON %q: %w", spec, err)
			}
			required := false
			if in.Required != nil {
				required = *in.Required
			}
			var def any
			if in.Default != nil {
				def = *in.Default
			}
			var help any
			if in.HelpText != nil {
				help = *in.HelpText
			}
			arg, err := buildSessionArg(in.Key, in.Label, in.Type, required, def, in.Options, help)
			if err != nil {
				return nil, err
			}
			out = append(out, arg)
			continue
		}

		// Shorthand: KEY=DEFAULT (optional) or KEY (required), always text.
		key := s
		var def any
		required := true
		if i := strings.IndexByte(s, '='); i >= 0 {
			key = s[:i]
			def = s[i+1:]
			required = false
		}
		arg, err := buildSessionArg(key, "", "text", required, def, nil, nil)
		if err != nil {
			return nil, err
		}
		out = append(out, arg)
	}
	return out, nil
}

// NewTemplateCommand returns `runtm template` for full org-template lifecycle.
//
// Org templates are pre-configured sandbox environments with repos, services,
// dependencies, and instructions baked in. Sessions created from a template
// boot instantly with the environment ready.
//
// All routes are dual-auth and live under /api/org-templates/*. Templates are
// org-scoped (--org or RUNTM_ORG_ID required).
//
// Surface covered:
//
//	GET    /api/org-templates                            templates:read    (list)
//	POST   /api/org-templates                            templates:write   (create)
//	GET    /api/org-templates/{id}                       templates:read    (get)
//	PATCH  /api/org-templates/{id}                       templates:write   (update)
//	DELETE /api/org-templates/{id}                       templates:delete  (delete)
//	POST   /api/org-templates/{id}/build                 templates:build   (build)
//	GET    /api/org-templates/{id}/build-logs            templates:read    (stream SSE)
//	GET    /api/org-templates/{id}/build-logs-history    templates:read    (history)
//	POST   /api/org-templates/{id}/fix-session           templates:write   (fix in sandbox)
//	POST   /api/org-templates/{id}/save-snapshot         templates:write   (save session as template)
//	GET    /api/org-templates/repos                      templates:read    (repo discovery)
//	GET    /api/org-templates/{id}/secrets               secrets:read      (template secrets)
//	PUT    /api/org-templates/{id}/secrets               secrets:write
//	DELETE /api/org-templates/{id}/secrets/{key}         secrets:write
func NewTemplateCommand(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "template",
		Short: "Full lifecycle for org templates: create, build, fix, snapshot, secrets",
		Long: `Org templates are pre-built sandbox snapshots with repos, services, secrets,
and agent instructions configured. Sessions launched from a template boot
instantly with the environment ready.

All template commands require --org or RUNTM_ORG_ID. Writes (create, build,
delete) require admin/owner role on the API key.

See https://docs.runtm.com/cloud-api/templates for the full schemas.`,
	}
	cmd.AddCommand(
		newTemplateList(rt),
		newTemplateGet(rt),
		newTemplateCreate(rt),
		newTemplateUpdate(rt),
		newTemplateDelete(rt),
		newTemplateBuild(rt),
		newTemplateBuildLogs(rt),
		newTemplateBuildLogsHistory(rt),
		newTemplateFixSession(rt),
		newTemplateSaveSnapshot(rt),
		newTemplateRepos(rt),
		newTemplateSecrets(rt),
		newTemplateDirectives(rt, "skills", "skill"),
		newTemplateDirectives(rt, "mcp", "mcp_server"),
	)
	return cmd
}

// --- attached skills / mcp servers ----------------------------------------

// newTemplateDirectives lists the skills or MCP servers attached to a template.
// It's the template-side view of the attachments managed by
// `runtm-api skills|mcp attach`: every session launched from the template loads
// these. Backed by GET /api/agent-directives?template_id=<id>&type_family=<f>.
func newTemplateDirectives(rt *Runtime, use, typeFamily string) *cobra.Command {
	var includeContent bool
	cmd := &cobra.Command{
		Use:   use + " <template_id>",
		Short: fmt.Sprintf("List %s attached to a template (sessions from it load these)", use),
		Long: fmt.Sprintf(`List the %s attached to a template. Attach/detach them with
'runtm-api %s attach|detach <id> --template <template_id>'.`, use, use),
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, "org templates")
			if err != nil {
				return err
			}
			q := url.Values{}
			q.Set("template_id", args[0])
			q.Set("type_family", typeFamily)
			if includeContent {
				q.Set("include_content", "true")
			}
			resp, err := c.Get(directivesListPath, q)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().BoolVar(&includeContent, "include-content", false, "Include each item's content payload")
	return cmd
}

// --- list / get -----------------------------------------------------------

func newTemplateList(rt *Runtime) *cobra.Command {
	var (
		pageSize  int
		pageToken string
	)
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List org templates (GET /api/org-templates)",
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, "org templates")
			if err != nil {
				return err
			}
			q := url.Values{}
			if pageSize > 0 {
				q.Set("page_size", strconv.Itoa(pageSize))
			}
			if pageToken != "" {
				q.Set("page_token", pageToken)
			}
			resp, err := c.Get("/org-templates", q)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().IntVar(&pageSize, "page-size", 0, "Results per page (1-100)")
	cmd.Flags().StringVar(&pageToken, "page-token", "", "Pagination cursor from a prior response")
	return cmd
}

func newTemplateGet(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "get <template_id>",
		Short: "Get a template (GET /api/org-templates/{id})",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, "org templates")
			if err != nil {
				return err
			}
			resp, err := c.Get("/org-templates/"+url.PathEscape(args[0]), nil)
			return runJSON(rt, resp, err)
		},
	}
}

// --- create / update / delete --------------------------------------------

func newTemplateCreate(rt *Runtime) *cobra.Command {
	var (
		displayName string
		name        string
		description string
		githubRepo  string
		branch      string
		tier        string
		build       bool
		skipAgent   bool
		sessionArgs []string
	)
	cmd := &cobra.Command{
		Use:   "create",
		Short: "Create a template from a GitHub repo (POST /api/org-templates)",
		Long: `Creates the template record in 'pending' state. Trigger the build with
'runtm template build <id>' after creation, or pass --build to kick it off in
the same call. --skip-agent (which implies --build) runs a clone-only build with
no AI step -- the same "Skip AI setup" path the dashboard uses.

Declare per-session arguments with --session-arg KEY=DEFAULT (repeatable); each
is collected when a member launches a session from the template and injected as
an environment variable. They are applied via a follow-up PATCH because the
create endpoint does not accept them directly. The template is org-scoped.`,
		RunE: func(cmd *cobra.Command, args []string) error {
			if displayName == "" || githubRepo == "" {
				return fmt.Errorf("--display-name and --github-repo are required")
			}
			c, _, err := requireOrgClient(rt, "org templates")
			if err != nil {
				return err
			}
			body := map[string]any{
				"display_name":  displayName,
				"github_repo":   githubRepo,
				"github_branch": branch,
				"tier":          tier,
			}
			if name != "" {
				body["name"] = name
			}
			if description != "" {
				body["description"] = description
			}
			resp, err := c.PostJSON("/org-templates", body)
			if err != nil {
				return runJSON(rt, resp, err)
			}

			// Nothing else to do -- emit the created template as-is.
			if len(sessionArgs) == 0 && !build && !skipAgent {
				return runJSON(rt, resp, nil)
			}

			var created struct {
				ID string `json:"id"`
			}
			if jerr := json.Unmarshal(resp, &created); jerr != nil || created.ID == "" {
				return fmt.Errorf("template created but its id could not be read for follow-up actions (set session args / build manually): %w", jerr)
			}
			path := "/org-templates/" + url.PathEscape(created.ID)

			// Session args can't be set at create time -- PATCH them in.
			if len(sessionArgs) > 0 {
				parsed, perr := parseSessionArgs(sessionArgs)
				if perr != nil {
					return perr
				}
				patchResp, patchErr := c.PatchJSON(path, map[string]any{"session_args": parsed})
				if patchErr != nil {
					return runJSON(rt, patchResp, patchErr)
				}
				// The PATCH response carries the session_args; surface it unless
				// we're also building (build response wins as the final state).
				if !build && !skipAgent {
					return runJSON(rt, patchResp, nil)
				}
			}

			// --skip-agent only makes sense alongside a build, so it implies it.
			buildResp, berr := c.PostJSON(path+"/build", map[string]any{"skip_agent": skipAgent})
			return runJSON(rt, buildResp, berr)
		},
	}
	cmd.Flags().StringVar(&displayName, "display-name", "", "Human-readable name (required)")
	cmd.Flags().StringVar(&name, "name", "", "Slug (auto-derived from repo if omitted)")
	cmd.Flags().StringVar(&description, "description", "", "Free-form description")
	cmd.Flags().StringVar(&githubRepo, "github-repo", "", "GitHub repo in owner/repo form (required)")
	cmd.Flags().StringVar(&branch, "github-branch", "main", "Branch to clone")
	cmd.Flags().StringVar(&tier, "tier", "basic", "Resource tier: basic, standard, max")
	cmd.Flags().BoolVar(&build, "build", false, "Trigger the build immediately after creating")
	cmd.Flags().BoolVar(&skipAgent, "skip-agent", false, "Clone-only build, no AI step (implies --build)")
	cmd.Flags().StringArrayVar(&sessionArgs, "session-arg", nil, `Declare a session argument (repeatable). Shorthand: KEY=DEFAULT (optional text) or KEY (required text). For select/boolean/label/help, pass JSON: '{"key":"ENV","type":"select","options":["dev","prod"],"default":"dev"}'.`)
	_ = cmd.MarkFlagRequired("display-name")
	_ = cmd.MarkFlagRequired("github-repo")
	return cmd
}

func newTemplateUpdate(rt *Runtime) *cobra.Command {
	var (
		displayName      string
		description      string
		sessionArgs      []string
		restartAtStartup bool
		startupScript    string
	)
	cmd := &cobra.Command{
		Use:   "update <template_id>",
		Short: "Update template metadata (PATCH /api/org-templates/{id})",
		Long: `Update template fields. --session-arg replaces the full set of session
arguments (pass it once per arg; omit to leave them unchanged). Shorthand:
KEY=DEFAULT (optional text) or KEY (required text). For select/boolean/label/
help, pass JSON per arg, e.g.
'{"key":"ENV","type":"select","options":["dev","prod"],"default":"dev"}'.`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, "org templates")
			if err != nil {
				return err
			}
			body := map[string]any{}
			if cmd.Flags().Changed("display-name") {
				body["display_name"] = displayName
			}
			if cmd.Flags().Changed("description") {
				body["description"] = description
			}
			if cmd.Flags().Changed("session-arg") {
				parsed, perr := parseSessionArgs(sessionArgs)
				if perr != nil {
					return perr
				}
				body["session_args"] = parsed
			}
			if cmd.Flags().Changed("restart-at-startup") {
				body["restart_at_startup"] = restartAtStartup
			}
			if cmd.Flags().Changed("startup-script") {
				body["startup_script"] = startupScript
			}
			if len(body) == 0 {
				return fmt.Errorf("pass at least one field to update (--display-name, --description, --session-arg, --restart-at-startup, --startup-script)")
			}
			resp, err := c.PatchJSON("/org-templates/"+url.PathEscape(args[0]), body)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&displayName, "display-name", "", "New display name")
	cmd.Flags().StringVar(&description, "description", "", "New description (pass empty string to clear)")
	cmd.Flags().StringArrayVar(&sessionArgs, "session-arg", nil, `Replace session args (repeatable). Shorthand KEY=DEFAULT / KEY, or JSON for select/boolean/label/help.`)
	cmd.Flags().BoolVar(&restartAtStartup, "restart-at-startup", false, "Force-restart services (and run the startup script) on first sandbox boot")
	cmd.Flags().StringVar(&startupScript, "startup-script", "", "Path to a startup script, relative to the workdir or absolute (empty string clears)")
	return cmd
}

func newTemplateDelete(rt *Runtime) *cobra.Command {
	var yes bool
	cmd := &cobra.Command{
		Use:   "delete <template_id>",
		Short: "Permanently delete a template (DELETE /api/org-templates/{id})",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if !yes {
				rt.WriteObject(map[string]any{
					"error": "Destructive operation requires --yes to confirm.",
					"hint":  "Pass --yes when you are sure.",
				})
				return errSilent
			}
			c, _, err := requireOrgClient(rt, "org templates")
			if err != nil {
				return err
			}
			resp, err := c.Delete("/org-templates/" + url.PathEscape(args[0]))
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().BoolVar(&yes, "yes", false, "Confirm deletion")
	return cmd
}

// --- build / build-logs --------------------------------------------------

func newTemplateBuild(rt *Runtime) *cobra.Command {
	var skipAgent bool
	cmd := &cobra.Command{
		Use:   "build <template_id>",
		Short: "Trigger a template build (POST /api/org-templates/{id}/build)",
		Long: `Returns 202 immediately; the build runs as a background job. Stream
progress with 'runtm template build-logs <id>'.

--skip-agent skips the AI agent build step (faster; the user completes
environment setup inside the session).`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, "org templates")
			if err != nil {
				return err
			}
			body := map[string]any{"skip_agent": skipAgent}
			resp, err := c.PostJSON("/org-templates/"+url.PathEscape(args[0])+"/build", body)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().BoolVar(&skipAgent, "skip-agent", false, "Skip the AI build step")
	return cmd
}

func newTemplateBuildLogs(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "build-logs <template_id>",
		Short: "Stream live build logs as SSE (GET /api/org-templates/{id}/build-logs)",
		Long:  "Each stdout line is a JSON envelope: {\"event\": \"<type>\", \"data\": <payload>}.",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, "org templates")
			if err != nil {
				return err
			}
			// SSE is a GET; reuse the streaming machinery by issuing a GET that
			// is then streamed. The client's StreamSSE is POST-only today, so
			// we use a thin direct call.
			return c.StreamSSEGet("/org-templates/"+url.PathEscape(args[0])+"/build-logs", rt.Stdout)
		},
	}
}

func newTemplateBuildLogsHistory(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "build-logs-history <template_id>",
		Short: "Get persisted build log history (GET /api/org-templates/{id}/build-logs-history)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, "org templates")
			if err != nil {
				return err
			}
			resp, err := c.Get("/org-templates/"+url.PathEscape(args[0])+"/build-logs-history", nil)
			return runJSON(rt, resp, err)
		},
	}
}

// --- fix-session / save-snapshot ----------------------------------------

func newTemplateFixSession(rt *Runtime) *cobra.Command {
	var agent string
	cmd := &cobra.Command{
		Use:     "fix-session <template_id>",
		Aliases: []string{"configure"},
		Short:   "Open a session on the template's sandbox to configure it manually",
		Long: `Spins up an interactive session that boots the template's sandbox so you (or
an agent) can configure it by hand. The response includes a session_id; connect
to it with 'runtm-api session connect <session_id>', make your changes, then run
'runtm-api template save-snapshot <template_id> --session <session_id>' to
promote the sandbox state into the template snapshot.

Also available as 'runtm-api template configure <template_id>'.
Required scope: templates:write.`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, "org templates")
			if err != nil {
				return err
			}
			body := map[string]any{"agent": agent}
			resp, err := c.PostJSON("/org-templates/"+url.PathEscape(args[0])+"/fix-session", body)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&agent, "agent", "claude-code", "Coding agent for the fix session")
	return cmd
}

func newTemplateSaveSnapshot(rt *Runtime) *cobra.Command {
	var sessionID string
	cmd := &cobra.Command{
		Use:   "save-snapshot <template_id>",
		Short: "Promote a session's sandbox state into the template snapshot",
		Long: `After fixing or extending a fix-session, save its sandbox as the template's
new snapshot. Existing sessions keep using the old snapshot until destroyed.`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if sessionID == "" {
				return fmt.Errorf("--session is required")
			}
			c, _, err := requireOrgClient(rt, "org templates")
			if err != nil {
				return err
			}
			body := map[string]any{"session_id": sessionID}
			resp, err := c.PostJSON("/org-templates/"+url.PathEscape(args[0])+"/save-snapshot", body)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&sessionID, "session", "", "Session ID whose sandbox to snapshot (required)")
	_ = cmd.MarkFlagRequired("session")
	return cmd
}

// --- repos -----------------------------------------------------------------

func newTemplateRepos(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "repos",
		Short: "Discover candidate GitHub repos for templates (GET /api/org-templates/repos)",
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, "org templates")
			if err != nil {
				return err
			}
			resp, err := c.Get("/org-templates/repos", nil)
			return runJSON(rt, resp, err)
		},
	}
}

// --- template secrets -----------------------------------------------------

func newTemplateSecrets(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "secrets",
		Short: "Manage per-template secrets (env vars injected at session boot)",
	}
	cmd.AddCommand(
		newTemplateSecretsList(rt),
		newTemplateSecretsSet(rt),
		newTemplateSecretsDelete(rt),
	)
	return cmd
}

func newTemplateSecretsList(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "list <template_id>",
		Short: "List template secret names (GET /api/org-templates/{id}/secrets)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, "org templates")
			if err != nil {
				return err
			}
			resp, err := c.Get("/org-templates/"+url.PathEscape(args[0])+"/secrets", nil)
			return runJSON(rt, resp, err)
		},
	}
}

func newTemplateSecretsSet(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "set <template_id> <name> <value> [<name> <value> ...]",
		Short: "Set template secrets (PUT /api/org-templates/{id}/secrets)",
		Args:  cobra.MinimumNArgs(3),
		RunE: func(cmd *cobra.Command, args []string) error {
			templateID := args[0]
			rest := args[1:]
			if len(rest)%2 != 0 {
				return fmt.Errorf("expected pairs of <name> <value>; got %d args", len(rest))
			}
			c, _, err := requireOrgClient(rt, "org templates")
			if err != nil {
				return err
			}
			secrets := make(map[string]string, len(rest)/2)
			for i := 0; i < len(rest); i += 2 {
				if rest[i] == "" {
					return fmt.Errorf("secret name at position %d is empty", i)
				}
				secrets[rest[i]] = rest[i+1]
			}
			body := map[string]any{"secrets": secrets}
			resp, err := c.PutJSON("/org-templates/"+url.PathEscape(templateID)+"/secrets", body)
			return runJSON(rt, resp, err)
		},
	}
	return cmd
}

func newTemplateSecretsDelete(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "delete <template_id> <name>",
		Short: "Delete a template secret (DELETE /api/org-templates/{id}/secrets/{key})",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, "org templates")
			if err != nil {
				return err
			}
			path := "/org-templates/" + url.PathEscape(args[0]) + "/secrets/" + url.PathEscape(args[1])
			resp, err := c.Delete(path)
			return runJSON(rt, resp, err)
		},
	}
}

// Ensure client package import is not orphaned if we add helpers later.
var _ = client.New
