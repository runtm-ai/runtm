package cmd

import (
	"fmt"
	"net/url"

	"github.com/runtm-ai/runtm/packages/agent/internal/client"
	"github.com/spf13/cobra"
)

// NewIntegrationsCommand returns `runtm integrations` covering provider API
// keys (Anthropic, OpenAI). Other integrations (GitHub App, Slack, Linear) are
// installed via OAuth flows that the CLI cannot drive end-to-end, so they are
// intentionally out of scope here.
//
// Routes:
//
//	GET    /api/user/anthropic-key                            integrations:read
//	PUT    /api/user/anthropic-key                            integrations:write
//	DELETE /api/user/anthropic-key                            integrations:write
//	GET    /api/user/anthropic-key/resolved                   integrations:read
//	GET    /api/user/openai-key                               integrations:read
//	PUT    /api/user/openai-key                               integrations:write
//	DELETE /api/user/openai-key                               integrations:write
//	GET    /api/user/openai-key/resolved                      integrations:read
//	GET    /api/organizations/{org_id}/anthropic-key          integrations:read
//	PUT    /api/organizations/{org_id}/anthropic-key          integrations:write (org admin)
//	DELETE /api/organizations/{org_id}/anthropic-key          integrations:write (org admin)
//	GET    /api/organizations/{org_id}/openai-key             integrations:read
//	PUT    /api/organizations/{org_id}/openai-key             integrations:write (org admin)
//	DELETE /api/organizations/{org_id}/openai-key             integrations:write (org admin)
func NewIntegrationsCommand(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "integrations",
		Short: "Manage Anthropic / OpenAI provider keys (user + org scope)",
		Long: `Stores encrypted provider API keys used by sessions when no per-session
override is set. Use --org to manage org-wide keys (admin/owner role required).

The CLI does not drive GitHub/Slack/Linear OAuth installations. Those flows
require browser redirects and live in the dashboard.

See https://docs.runtm.com/cloud-api/provider-keys for the schema.`,
	}
	cmd.AddCommand(
		newIntegrationsKeyCommand(rt, "anthropic"),
		newIntegrationsKeyCommand(rt, "openai"),
	)
	return cmd
}

// newIntegrationsKeyCommand builds the `anthropic` / `openai` subcommand
// trees. Each has get/set/delete plus a `resolved` variant for the personal
// scope only (the resolved endpoint follows team -> user precedence).
func newIntegrationsKeyCommand(rt *Runtime, provider string) *cobra.Command {
	cmd := &cobra.Command{
		Use:   provider,
		Short: fmt.Sprintf("Manage the %s provider key", provider),
	}
	cmd.AddCommand(
		newIntegrationsGet(rt, provider),
		newIntegrationsSet(rt, provider),
		newIntegrationsDelete(rt, provider),
		newIntegrationsResolved(rt, provider),
	)
	return cmd
}

func newIntegrationsGet(rt *Runtime, provider string) *cobra.Command {
	var orgScope bool
	cmd := &cobra.Command{
		Use:   "get",
		Short: fmt.Sprintf("Inspect stored %s key (masked)", provider),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, path, err := integrationsPath(rt, provider, orgScope, "")
			if err != nil {
				return err
			}
			resp, err := c.Get(path, nil)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().BoolVar(&orgScope, "org-scope", false, "Read org-level key (requires --org)")
	return cmd
}

func newIntegrationsSet(rt *Runtime, provider string) *cobra.Command {
	var (
		orgScope bool
		apiKey   string
		policy   string
	)
	cmd := &cobra.Command{
		Use:   "set",
		Short: fmt.Sprintf("Store a new %s key", provider),
		RunE: func(cmd *cobra.Command, args []string) error {
			if apiKey == "" {
				return fmt.Errorf("--api-key is required")
			}
			c, path, err := integrationsPath(rt, provider, orgScope, "")
			if err != nil {
				return err
			}
			body := map[string]any{"api_key": apiKey}
			if policy != "" {
				body["policy"] = policy
			}
			resp, err := c.PutJSON(path, body)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().BoolVar(&orgScope, "org-scope", false, "Write to org-level key (requires --org and admin role)")
	cmd.Flags().StringVar(&apiKey, "api-key", "", "Raw provider API key (required)")
	cmd.Flags().StringVar(&policy, "policy", "", "Org policy: 'allow_user_override' or 'require_team_key' (org only)")
	_ = cmd.MarkFlagRequired("api-key")
	return cmd
}

func newIntegrationsDelete(rt *Runtime, provider string) *cobra.Command {
	var orgScope bool
	cmd := &cobra.Command{
		Use:   "delete",
		Short: fmt.Sprintf("Remove the stored %s key", provider),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, path, err := integrationsPath(rt, provider, orgScope, "")
			if err != nil {
				return err
			}
			resp, err := c.Delete(path)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().BoolVar(&orgScope, "org-scope", false, "Delete org-level key (requires --org and admin role)")
	return cmd
}

func newIntegrationsResolved(rt *Runtime, provider string) *cobra.Command {
	return &cobra.Command{
		Use:   "resolved",
		Short: fmt.Sprintf("Show which %s key would be used (team override -> user fallback)", provider),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, path, err := integrationsPath(rt, provider, false, "/resolved")
			if err != nil {
				return err
			}
			resp, err := c.Get(path, nil)
			return runJSON(rt, resp, err)
		},
	}
}

// integrationsPath builds /api/user/{provider}-key or /api/organizations/{org}/{provider}-key
// plus an optional suffix (e.g. "/resolved").
func integrationsPath(rt *Runtime, provider string, orgScope bool, suffix string) (*client.Client, string, error) {
	if orgScope {
		c, creds, err := requireOrgClient(rt, "org provider keys")
		if err != nil {
			return nil, "", err
		}
		return c, "/organizations/" + url.PathEscape(creds.OrganizationID) + "/" + provider + "-key" + suffix, nil
	}
	c, _, err := rt.Client()
	if err != nil {
		return nil, "", err
	}
	return c, "/user/" + provider + "-key" + suffix, nil
}
