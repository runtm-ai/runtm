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
			// Surface the effective org context: explicit (--org / env)
			// first, otherwise whatever is embedded in the key (returned
			// by /v1/me as tenant_id when the key is org-scoped).
			if creds.OrganizationID != "" {
				out["organization_id"] = creds.OrganizationID
			} else if t, ok := me["tenant_id"].(string); ok && t != "" {
				out["organization_id"] = t
			}
			for k, v := range me {
				out[k] = v
			}
			rt.WriteObject(out)
			return nil
		},
	}
}

// errSilent is a sentinel: the command already printed structured output and
// the runner should exit with a specific code without re-reporting.
var errSilent = errors.New("silent")
