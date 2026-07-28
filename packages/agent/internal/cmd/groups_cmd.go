package cmd

import (
	"net/url"

	"github.com/spf13/cobra"
)

// NewGroupsCommand returns `runtm-api groups` for owning-group introspection.
//
// Groups (Better Auth teams) own templates and skills via owner_team_id:
// group-owned resources are hidden from non-members. Set ownership with
// 'template update --owner-team' / 'skills update --owner-team'; this command
// answers "what does this group own?" before renaming or deleting it.
//
// Routes:
//
//	GET /api/groups/{team_id}/usage    templates:read
func NewGroupsCommand(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "groups",
		Short: "Inspect owning groups (Better Auth teams) and what they own",
		Long: `Groups scope templates and skills to a subset of the org: a resource with
an owner_team_id is visible only to that group's members (and org admins).

Assign ownership with the --owner-team flag on 'template update',
'skills update', and 'mcp update'. Group membership itself is managed in
the dashboard (Settings > Team > Groups).`,
	}
	cmd.AddCommand(newGroupsUsage(rt))
	return cmd
}

func newGroupsUsage(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "usage <team_id>",
		Short: "What a group owns (GET /api/groups/{team_id}/usage)",
		Long: `Counts the templates and skills owned by the group, with up to five names
of each, and whether the group is in use at all. Check before deleting a
group: deletion is blocked while it still owns resources.`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, "groups")
			if err != nil {
				return err
			}
			resp, err := c.Get("/groups/"+url.PathEscape(args[0])+"/usage", nil)
			return runJSON(rt, resp, err)
		},
	}
}
