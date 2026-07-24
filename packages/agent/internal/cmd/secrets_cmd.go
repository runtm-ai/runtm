package cmd

import (
	"fmt"
	"net/url"

	"github.com/runtm-ai/runtm/packages/agent/internal/client"
	"github.com/spf13/cobra"
)

// NewSecretsCommand returns `runtm secrets` for personal and team secrets.
//
// Routes (canonical /api/secrets/*, all dual-auth):
//
//	GET    /api/secrets/personal             secrets:read
//	PUT    /api/secrets/personal             secrets:write
//	DELETE /api/secrets/personal/{name}      secrets:write
//	GET    /api/secrets/team                 secrets:read   (org required)
//	PUT    /api/secrets/team                 secrets:write  (org required)
//	DELETE /api/secrets/team/{name}          secrets:write  (org required)
//	GET    /api/secrets/resolved             secrets:read
//
// Secret values are never returned; list endpoints expose names and masked
// previews only.
func NewSecretsCommand(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "secrets",
		Short: "Manage personal and team secrets (values never returned)",
		Long: `Personal secrets belong to the API key owner. Team secrets are org-scoped
and require admin/owner role on the key for writes.

Set --team to operate on team secrets (requires an org-scoped API key).
See https://docs.runtm.com/cloud-api/secrets for the full schemas.`,
	}
	cmd.AddCommand(
		newSecretsList(rt),
		newSecretsSet(rt),
		newSecretsDelete(rt),
		newSecretsResolved(rt),
	)
	return cmd
}

func newSecretsList(rt *Runtime) *cobra.Command {
	var team bool
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List secret names",
		RunE: func(cmd *cobra.Command, args []string) error {
			c, err := pickSecretsClient(rt, team)
			if err != nil {
				return err
			}
			resp, err := c.Get(secretsPath(team, ""), nil)
			if err != nil {
				return err
			}
			rt.WriteJSON(resp)
			return nil
		},
	}
	cmd.Flags().BoolVar(&team, "team", false, "List team secrets (requires an org-scoped key)")
	return cmd
}

func newSecretsSet(rt *Runtime) *cobra.Command {
	var team bool
	cmd := &cobra.Command{
		Use:   "set <name> <value> [<name> <value> ...]",
		Short: "Create or update secrets",
		Long: `Set one or more secret key/value pairs in positional order:
  runtm secrets set DATABASE_URL "postgres://..." API_KEY abc123`,
		Args: cobra.MinimumNArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			if len(args)%2 != 0 {
				return fmt.Errorf("expected pairs of <name> <value>; got %d args", len(args))
			}
			c, err := pickSecretsClient(rt, team)
			if err != nil {
				return err
			}
			items := make([]map[string]any, 0, len(args)/2)
			for i := 0; i < len(args); i += 2 {
				if args[i] == "" {
					return fmt.Errorf("secret name at position %d is empty", i)
				}
				items = append(items, map[string]any{
					"name":  args[i],
					"value": args[i+1],
				})
			}
			resp, err := c.PutJSON(secretsPath(team, ""), map[string]any{"secrets": items})
			if err != nil {
				return err
			}
			rt.WriteJSON(resp)
			return nil
		},
	}
	cmd.Flags().BoolVar(&team, "team", false, "Write to team secrets (requires an org-scoped key and admin role)")
	return cmd
}

func newSecretsDelete(rt *Runtime) *cobra.Command {
	var team bool
	cmd := &cobra.Command{
		Use:   "delete <name>",
		Short: "Delete a secret",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, err := pickSecretsClient(rt, team)
			if err != nil {
				return err
			}
			resp, err := c.Delete(secretsPath(team, args[0]))
			if err != nil {
				return err
			}
			rt.WriteJSON(resp)
			return nil
		},
	}
	cmd.Flags().BoolVar(&team, "team", false, "Delete from team secrets (requires an org-scoped key and admin role)")
	return cmd
}

func newSecretsResolved(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "resolved",
		Short: "Resolved secret names after merging team + personal",
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			resp, err := c.Get("/secrets/resolved", nil)
			if err != nil {
				return err
			}
			rt.WriteJSON(resp)
			return nil
		},
	}
}

func pickSecretsClient(rt *Runtime, team bool) (*client.Client, error) {
	if team {
		c, _, err := requireOrgClient(rt, "team secrets")
		return c, err
	}
	c, _, err := rt.Client()
	return c, err
}

func secretsPath(team bool, name string) string {
	base := "/secrets/personal"
	if team {
		base = "/secrets/team"
	}
	if name == "" {
		return base
	}
	return base + "/" + url.PathEscape(name)
}
