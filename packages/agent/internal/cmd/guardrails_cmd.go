package cmd

import (
	"net/url"

	"github.com/runtm-ai/runtm/packages/agent/internal/client"
	"github.com/spf13/cobra"
)

// NewGuardrailsCommand returns `runtm guardrails` for org-scoped limits,
// allowlist policy, and deploy permission checks.
//
// Routes:
//
//	GET    /api/organizations/{org_id}/limits           guardrails:read
//	PUT    /api/organizations/{org_id}/limits           guardrails:write
//	GET    /api/organizations/{org_id}/allowlist-policy guardrails:read
//	PUT    /api/organizations/{org_id}/allowlist-policy guardrails:write
//	GET    /api/organizations/{org_id}/can-deploy       deployments:read
//	DELETE /api/organizations/{org_id}/cleanup          guardrails:write
//	GET    /api/deploy/limits                           deployments:read
//
// Limits and allowlist mutations require admin/owner role on the API key.
func NewGuardrailsCommand(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "guardrails",
		Short: "Org spend limits, allowlist policy, and deploy permissions",
		Long: `Inspect and set org-scoped policies that gate session and deploy spend.
Most commands require --org or RUNTM_ORG_ID. Writes require admin/owner role.

See https://docs.runtm.com/cloud-api/guardrails for the full schemas.`,
	}
	cmd.AddCommand(
		newGuardrailsLimits(rt),
		newGuardrailsAllowlist(rt),
		newGuardrailsCanDeploy(rt),
		newGuardrailsDeployLimits(rt),
		newGuardrailsCleanup(rt),
	)
	return cmd
}

func newGuardrailsLimits(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "limits",
		Short: "Get or update org compute / cost limits",
	}
	cmd.AddCommand(
		&cobra.Command{
			Use:   "get",
			Short: "Read current org limits (GET /api/organizations/{org_id}/limits)",
			RunE: func(cmd *cobra.Command, args []string) error {
				c, path, err := orgGuardrailsPath(rt, "limits")
				if err != nil {
					return err
				}
				resp, err := c.Get(path, nil)
				return runJSON(rt, resp, err)
			},
		},
		newGuardrailsLimitsSet(rt),
	)
	return cmd
}

func newGuardrailsLimitsSet(rt *Runtime) *cobra.Command {
	var (
		maxConcurrent int
		monthlyUSD    float64
		idleTimeout   int
	)
	cmd := &cobra.Command{
		Use:   "set",
		Short: "Update org limits (PUT /api/organizations/{org_id}/limits)",
		RunE: func(cmd *cobra.Command, args []string) error {
			c, path, err := orgGuardrailsPath(rt, "limits")
			if err != nil {
				return err
			}
			body := map[string]any{}
			if cmd.Flags().Changed("max-concurrent-sessions") {
				body["max_concurrent_sessions"] = maxConcurrent
			}
			if cmd.Flags().Changed("monthly-budget-usd") {
				body["monthly_budget_usd"] = monthlyUSD
			}
			if cmd.Flags().Changed("idle-timeout-minutes") {
				body["idle_timeout_minutes"] = idleTimeout
			}
			resp, err := c.PutJSON(path, body)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().IntVar(&maxConcurrent, "max-concurrent-sessions", 0, "Max sessions running concurrently")
	cmd.Flags().Float64Var(&monthlyUSD, "monthly-budget-usd", 0, "Monthly USD budget cap")
	cmd.Flags().IntVar(&idleTimeout, "idle-timeout-minutes", 0, "Default idle timeout for new sessions")
	return cmd
}

func newGuardrailsAllowlist(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "allowlist",
		Short: "Get or update org GitHub allowlist policy",
	}
	cmd.AddCommand(
		&cobra.Command{
			Use:   "get",
			Short: "Read allowlist (GET /api/organizations/{org_id}/allowlist-policy)",
			RunE: func(cmd *cobra.Command, args []string) error {
				c, path, err := orgGuardrailsPath(rt, "allowlist-policy")
				if err != nil {
					return err
				}
				resp, err := c.Get(path, nil)
				return runJSON(rt, resp, err)
			},
		},
		newGuardrailsAllowlistSet(rt),
	)
	return cmd
}

func newGuardrailsAllowlistSet(rt *Runtime) *cobra.Command {
	var (
		mode         string
		patternsCSV  string
		allowPersonal bool
	)
	cmd := &cobra.Command{
		Use:   "set",
		Short: "Update allowlist (PUT /api/organizations/{org_id}/allowlist-policy)",
		RunE: func(cmd *cobra.Command, args []string) error {
			c, path, err := orgGuardrailsPath(rt, "allowlist-policy")
			if err != nil {
				return err
			}
			body := map[string]any{}
			if cmd.Flags().Changed("mode") {
				body["mode"] = mode
			}
			if cmd.Flags().Changed("patterns") {
				body["repo_patterns"] = splitCSV(patternsCSV)
			}
			if cmd.Flags().Changed("allow-personal") {
				body["allow_personal_repos"] = allowPersonal
			}
			resp, err := c.PutJSON(path, body)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&mode, "mode", "", "Allowlist mode: 'all' or 'allowlist'")
	cmd.Flags().StringVar(&patternsCSV, "patterns", "", "Comma-separated repo patterns (e.g. 'acme/*,internal/*')")
	cmd.Flags().BoolVar(&allowPersonal, "allow-personal", false, "Allow members to clone personal repos")
	return cmd
}

func newGuardrailsCanDeploy(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "can-deploy",
		Short: "Check deploy permission for the active org (GET /api/organizations/{org_id}/can-deploy)",
		RunE: func(cmd *cobra.Command, args []string) error {
			c, path, err := orgGuardrailsPath(rt, "can-deploy")
			if err != nil {
				return err
			}
			resp, err := c.Get(path, nil)
			return runJSON(rt, resp, err)
		},
	}
}

func newGuardrailsDeployLimits(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "deploy-limits",
		Short: "Get effective deployment limits (GET /api/deploy/limits)",
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			resp, err := c.Get("/deploy/limits", nil)
			return runJSON(rt, resp, err)
		},
	}
}

func newGuardrailsCleanup(rt *Runtime) *cobra.Command {
	var yes bool
	cmd := &cobra.Command{
		Use:   "cleanup",
		Short: "Force-destroy stuck org sessions (DELETE /api/organizations/{org_id}/cleanup, admin only)",
		RunE: func(cmd *cobra.Command, args []string) error {
			if !yes {
				rt.WriteObject(map[string]any{
					"error": "Destructive operation requires --yes to confirm.",
					"hint":  "Cleanup tears down all stuck org sessions. Pass --yes when you are sure.",
				})
				return errSilent
			}
			c, path, err := orgGuardrailsPath(rt, "cleanup")
			if err != nil {
				return err
			}
			resp, err := c.Delete(path)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().BoolVar(&yes, "yes", false, "Confirm the destructive action")
	return cmd
}

func orgGuardrailsPath(rt *Runtime, suffix string) (*client.Client, string, error) {
	c, creds, err := requireOrgClient(rt, "org guardrails")
	if err != nil {
		return nil, "", err
	}
	path := "/organizations/" + url.PathEscape(creds.OrganizationID) + "/" + suffix
	return c, path, nil
}

// runJSON pipes a (resp, err) tuple through rt.WriteJSON.
func runJSON(rt *Runtime, resp []byte, err error) error {
	if err != nil {
		return err
	}
	rt.WriteJSON(resp)
	return nil
}

func splitCSV(s string) []string {
	if s == "" {
		return []string{}
	}
	out := []string{}
	current := ""
	for i := 0; i < len(s); i++ {
		if s[i] == ',' {
			if current != "" {
				out = append(out, current)
			}
			current = ""
			continue
		}
		current += string(s[i])
	}
	if current != "" {
		out = append(out, current)
	}
	return out
}
