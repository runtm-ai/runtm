package cmd

import (
	"fmt"
	"net/url"

	"github.com/runtm-ai/runtm/packages/agent/internal/client"
	"github.com/spf13/cobra"
)

// NewInstructionsCommand returns `runtm instructions` for custom CLAUDE.md
// content injected into every session.
//
// Routes:
//
//	GET /api/user/instructions                            context:read
//	PUT /api/user/instructions                            context:write
//	GET /api/organizations/{org_id}/instructions          context:read   (org required)
//	PUT /api/organizations/{org_id}/instructions          context:write  (org admin)
func NewInstructionsCommand(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "instructions",
		Short: "Get and set custom user / org instructions injected into sessions",
		Long: `Custom instructions are appended to CLAUDE.md in every session, so the
agent always reads them at boot. User instructions apply to your sessions;
org instructions apply to every session inside the org.

Use --org or RUNTM_ORG_ID to scope to an organization.
See https://docs.runtm.com/cloud-api/context for the full schema.`,
	}
	cmd.AddCommand(newInstructionsGet(rt), newInstructionsSet(rt))
	return cmd
}

func newInstructionsGet(rt *Runtime) *cobra.Command {
	var orgScope bool
	cmd := &cobra.Command{
		Use:   "get",
		Short: "Read current instructions",
		RunE: func(cmd *cobra.Command, args []string) error {
			c, path, err := instructionsTarget(rt, orgScope)
			if err != nil {
				return err
			}
			resp, err := c.Get(path, nil)
			if err != nil {
				return err
			}
			rt.WriteJSON(resp)
			return nil
		},
	}
	cmd.Flags().BoolVar(&orgScope, "org-scope", false, "Read org instructions (requires --org)")
	return cmd
}

func newInstructionsSet(rt *Runtime) *cobra.Command {
	var (
		orgScope bool
		text     string
		clear    bool
	)
	cmd := &cobra.Command{
		Use:   "set",
		Short: "Update instructions (use --text or --clear)",
		RunE: func(cmd *cobra.Command, args []string) error {
			if text == "" && !clear {
				return fmt.Errorf("provide --text or --clear")
			}
			c, path, err := instructionsTarget(rt, orgScope)
			if err != nil {
				return err
			}
			body := map[string]any{}
			if clear {
				body["instructions"] = nil
			} else {
				body["instructions"] = text
			}
			resp, err := c.PutJSON(path, body)
			if err != nil {
				return err
			}
			rt.WriteJSON(resp)
			return nil
		},
	}
	cmd.Flags().BoolVar(&orgScope, "org-scope", false, "Write org instructions (requires --org and admin role)")
	cmd.Flags().StringVarP(&text, "text", "t", "", "Instructions content")
	cmd.Flags().BoolVar(&clear, "clear", false, "Clear instructions (set to null)")
	return cmd
}

// instructionsTarget picks the user or org path and returns a configured client.
func instructionsTarget(rt *Runtime, orgScope bool) (*client.Client, string, error) {
	if orgScope {
		c, creds, err := requireOrgClient(rt, "org instructions")
		if err != nil {
			return nil, "", err
		}
		return c, "/organizations/" + url.PathEscape(creds.OrganizationID) + "/instructions", nil
	}
	c, _, err := rt.Client()
	if err != nil {
		return nil, "", err
	}
	return c, "/user/instructions", nil
}
