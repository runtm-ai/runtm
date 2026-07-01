package cmd

import (
	"fmt"
	"net/url"

	"github.com/runtm-ai/runtm/packages/agent/internal/client"
	"github.com/spf13/cobra"
)

// NewProvidersCommand returns `runtm providers` covering LLM provider API keys
// (Anthropic, OpenAI). This is deliberately separate from "integrations", which
// means external integrations (MCP servers, skills, tools, CLIs, APIs) — see the
// `runtm-integrations` skill. External integration OAuth flows (GitHub App,
// Slack, Linear) require browser redirects the CLI cannot drive, so they live in
// the dashboard.
//
// The backend scope is still integrations:read / integrations:write (the command
// was renamed; the scope namespace was not).
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
func NewProvidersCommand(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "providers",
		Short: "Manage Anthropic / OpenAI LLM provider keys (user + org scope)",
		Long: `Stores encrypted LLM provider API keys used by sessions when no per-session
override is set. Use --org to manage org-wide keys (admin/owner role required).

This is separate from external integrations (MCP servers, skills, tools, CLIs,
APIs) — for those, see the 'runtm-integrations' skill. GitHub/Slack/Linear OAuth
installations also live in the dashboard, not here.

See https://docs.runtm.com/cloud-api/provider-keys for the schema.`,
	}
	cmd.AddCommand(
		newProviderKeyCommand(rt, "anthropic"),
		newProviderKeyCommand(rt, "openai"),
	)
	return cmd
}

// newProviderKeyCommand builds the `anthropic` / `openai` subcommand trees.
// Each has get/set/delete plus a `resolved` variant for the personal scope only
// (the resolved endpoint follows team -> user precedence).
func newProviderKeyCommand(rt *Runtime, provider string) *cobra.Command {
	cmd := &cobra.Command{
		Use:   provider,
		Short: fmt.Sprintf("Manage the %s provider key", provider),
	}
	cmd.AddCommand(
		newProviderGet(rt, provider),
		newProviderSet(rt, provider),
		newProviderDelete(rt, provider),
		newProviderResolved(rt, provider),
	)
	return cmd
}

func newProviderGet(rt *Runtime, provider string) *cobra.Command {
	var orgScope bool
	cmd := &cobra.Command{
		Use:   "get",
		Short: fmt.Sprintf("Inspect stored %s key (masked)", provider),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, path, err := providerKeyPath(rt, provider, orgScope, "")
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

func newProviderSet(rt *Runtime, provider string) *cobra.Command {
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
			c, path, err := providerKeyPath(rt, provider, orgScope, "")
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

func newProviderDelete(rt *Runtime, provider string) *cobra.Command {
	var orgScope bool
	cmd := &cobra.Command{
		Use:   "delete",
		Short: fmt.Sprintf("Remove the stored %s key", provider),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, path, err := providerKeyPath(rt, provider, orgScope, "")
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

func newProviderResolved(rt *Runtime, provider string) *cobra.Command {
	return &cobra.Command{
		Use:   "resolved",
		Short: fmt.Sprintf("Show which %s key would be used (team override -> user fallback)", provider),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, path, err := providerKeyPath(rt, provider, false, "/resolved")
			if err != nil {
				return err
			}
			resp, err := c.Get(path, nil)
			return runJSON(rt, resp, err)
		},
	}
}

// providerKeyPath builds /api/user/{provider}-key or /api/organizations/{org}/{provider}-key
// plus an optional suffix (e.g. "/resolved").
func providerKeyPath(rt *Runtime, provider string, orgScope bool, suffix string) (*client.Client, string, error) {
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
