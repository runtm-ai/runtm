package cmd

import (
	"net/url"
	"strconv"

	"github.com/spf13/cobra"
)

// NewActivityCommand returns `runtm activity` -- read-only telemetry queries.
//
// All routes mount under /api/sessions/telemetry/* and require the
// `activity:read` scope. Team endpoints additionally require an organization
// context (--org or RUNTM_ORG_ID).
func NewActivityCommand(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "activity",
		Short: "Inspect prompt activity, cost, and usage telemetry",
		Long: `Read-only access to session telemetry: personal summaries, recent prompts,
per-session usage, and team-wide rollups. All endpoints require the
'activity:read' scope. Team commands additionally require an org context.

See https://docs.runtm.com/cloud-api/activity for the full schemas.`,
	}
	cmd.AddCommand(
		newActivitySummary(rt),
		newActivityRecentPrompts(rt),
		newActivityDaily(rt),
		newActivitySessionUsage(rt),
		newActivityTeamSummary(rt),
		newActivityTeamActivity(rt),
		newActivityTeamMembers(rt),
	)
	return cmd
}

func newActivitySummary(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "summary",
		Short: "Personal activity summary (GET /api/sessions/telemetry/summary)",
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			resp, err := c.Get("/sessions/telemetry/summary", nil)
			if err != nil {
				return err
			}
			rt.WriteJSON(resp)
			return nil
		},
	}
}

func newActivityRecentPrompts(rt *Runtime) *cobra.Command {
	var limit int
	cmd := &cobra.Command{
		Use:   "recent-prompts",
		Short: "Recent prompts (GET /api/sessions/telemetry/prompts)",
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			q := url.Values{}
			if limit > 0 {
				q.Set("limit", strconv.Itoa(limit))
			}
			resp, err := c.Get("/sessions/telemetry/prompts", q)
			if err != nil {
				return err
			}
			rt.WriteJSON(resp)
			return nil
		},
	}
	cmd.Flags().IntVar(&limit, "limit", 0, "Max prompts to return (default server-side)")
	return cmd
}

func newActivityDaily(rt *Runtime) *cobra.Command {
	var days int
	cmd := &cobra.Command{
		Use:   "daily",
		Short: "Daily personal activity (GET /api/sessions/telemetry/activity)",
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			q := url.Values{}
			if days > 0 {
				q.Set("days", strconv.Itoa(days))
			}
			resp, err := c.Get("/sessions/telemetry/activity", q)
			if err != nil {
				return err
			}
			rt.WriteJSON(resp)
			return nil
		},
	}
	cmd.Flags().IntVar(&days, "days", 0, "Number of days to return (default server-side)")
	return cmd
}

func newActivitySessionUsage(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "session-usage <session_id>",
		Short: "Per-session usage details (GET /api/sessions/{id}/usage)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			resp, err := c.Get("/sessions/"+url.PathEscape(args[0])+"/usage", nil)
			if err != nil {
				return err
			}
			rt.WriteJSON(resp)
			return nil
		},
	}
}

func newActivityTeamSummary(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "team-summary",
		Short: "Team-wide summary (GET /api/sessions/telemetry/team/summary, requires --org)",
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, "team telemetry")
			if err != nil {
				return err
			}
			resp, err := c.Get("/sessions/telemetry/team/summary", nil)
			if err != nil {
				return err
			}
			rt.WriteJSON(resp)
			return nil
		},
	}
}

func newActivityTeamActivity(rt *Runtime) *cobra.Command {
	var days int
	cmd := &cobra.Command{
		Use:   "team-activity",
		Short: "Team activity over time (GET /api/sessions/telemetry/team/activity, requires --org)",
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, "team telemetry")
			if err != nil {
				return err
			}
			q := url.Values{}
			if days > 0 {
				q.Set("days", strconv.Itoa(days))
			}
			resp, err := c.Get("/sessions/telemetry/team/activity", q)
			if err != nil {
				return err
			}
			rt.WriteJSON(resp)
			return nil
		},
	}
	cmd.Flags().IntVar(&days, "days", 0, "Number of days to include")
	return cmd
}

func newActivityTeamMembers(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "team-members",
		Short: "Per-member team usage (GET /api/sessions/telemetry/team/members, requires --org)",
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, "team telemetry")
			if err != nil {
				return err
			}
			resp, err := c.Get("/sessions/telemetry/team/members", nil)
			if err != nil {
				return err
			}
			rt.WriteJSON(resp)
			return nil
		},
	}
}
