package cmd

import (
	"fmt"
	"net/url"
	"strconv"

	"github.com/runtm-ai/runtm/packages/agent/internal/client"
	"github.com/spf13/cobra"
)

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
	)
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
	)
	cmd := &cobra.Command{
		Use:   "create",
		Short: "Create a template from a GitHub repo (POST /api/org-templates)",
		Long: `Creates the template record in 'pending' state. Trigger the build with
'runtm template build <id>' after creation. The template is org-scoped.`,
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
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&displayName, "display-name", "", "Human-readable name (required)")
	cmd.Flags().StringVar(&name, "name", "", "Slug (auto-derived from repo if omitted)")
	cmd.Flags().StringVar(&description, "description", "", "Free-form description")
	cmd.Flags().StringVar(&githubRepo, "github-repo", "", "GitHub repo in owner/repo form (required)")
	cmd.Flags().StringVar(&branch, "github-branch", "main", "Branch to clone")
	cmd.Flags().StringVar(&tier, "tier", "basic", "Resource tier: basic, standard, max")
	_ = cmd.MarkFlagRequired("display-name")
	_ = cmd.MarkFlagRequired("github-repo")
	return cmd
}

func newTemplateUpdate(rt *Runtime) *cobra.Command {
	var (
		displayName string
		description string
	)
	cmd := &cobra.Command{
		Use:   "update <template_id>",
		Short: "Update template metadata (PATCH /api/org-templates/{id})",
		Args:  cobra.ExactArgs(1),
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
			if len(body) == 0 {
				return fmt.Errorf("pass at least one field to update (--display-name, --description)")
			}
			resp, err := c.PatchJSON("/org-templates/"+url.PathEscape(args[0]), body)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&displayName, "display-name", "", "New display name")
	cmd.Flags().StringVar(&description, "description", "", "New description (pass empty string to clear)")
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
		Use:   "fix-session <template_id>",
		Short: "Create a session against the template sandbox for iterative fixes",
		Long: `Spins up an interactive session that boots the template's sandbox so an
agent can investigate and fix issues. After the agent confirms the fix, call
'runtm template save-snapshot <template_id> --session <session_id>' to
promote the session state into the template snapshot.

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
