package cmd

import (
	"encoding/json"
	"errors"

	"github.com/runtm-ai/runtm/packages/agent/internal/auth"
	"github.com/spf13/cobra"
)

// NewAuthCommand returns `runtm auth` with subcommands.
func NewAuthCommand(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "auth",
		Short: "Inspect API key, scopes, and org context",
	}
	cmd.AddCommand(newAuthStatusCommand(rt))
	return cmd
}

func newAuthStatusCommand(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "status",
		Short: "Verify the current API key against /api/v1/me",
		RunE: func(cmd *cobra.Command, args []string) error {
			c, creds, err := rt.Client()
			if err != nil {
				// auth status is the one command where missing creds should
				// still produce structured output rather than an error.
				if errors.Is(err, auth.ErrNoCredentials) {
					rt.WriteObject(map[string]any{
						"authenticated": false,
						"error":         err.Error(),
						"hint":          "Set RUNTM_API_KEY or run `runtm login` (pip CLI).",
					})
					return errSilent
				}
				return err
			}

			body, err := c.Get("/v1/me", nil)
			if err != nil {
				return err
			}

			var me map[string]any
			if jsonErr := json.Unmarshal(body, &me); jsonErr != nil {
				rt.WriteJSON(body)
				return nil
			}

			out := map[string]any{
				"authenticated": true,
				"api_url":       creds.APIURL,
				"source":        creds.Source,
			}
			for k, v := range me {
				out[k] = v
			}

			// Surface the effective org context: explicit (--org / env)
			// first, otherwise whatever the key itself is bound to. Resolved
			// after copying `me` so a null organization_id in the payload
			// cannot clobber it, and reported identically to the bootstrap
			// that org-scoped commands rely on.
			str := func(key string) string {
				s, _ := me[key].(string)
				return s
			}
			orgID := creds.OrganizationID
			if orgID == "" {
				orgID = orgFromIdentity(str("organization_id"), str("tenant_id"), str("principal_id"))
			}
			if orgID != "" {
				out["organization_id"] = orgID
			} else {
				delete(out, "organization_id")
			}
			rt.WriteObject(out)
			return nil
		},
	}
}

// errSilent is a sentinel: the command already printed structured output and
// the runner should exit with a specific code without re-reporting.
var errSilent = errors.New("silent")
