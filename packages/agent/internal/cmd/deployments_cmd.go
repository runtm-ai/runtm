package cmd

import (
	"net/url"
	"strconv"

	"github.com/spf13/cobra"
)

// NewDeploymentsCommand returns `runtm-api deployments` (alias `deploy`) for
// the deployments that `session deploy run` produces. Previously the CLI
// could ship a deployment but never see it again: list, inspect, logs, and
// destroy lived only in the pip CLI.
//
// Routes (API-key auth via the deployments proxy):
//
//	GET    /api/v0/deployments                    deployments:read
//	GET    /api/v0/deployments/{id}               deployments:read
//	GET    /api/v0/deployments/{id}/logs          deployments:read
//	DELETE /api/v0/deployments/{id}               deployments:delete
func NewDeploymentsCommand(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:     "deployments",
		Aliases: []string{"deploy", "deploys"},
		Short:   "List, inspect, and destroy deployments",
		Long: `Manage the deployments created from sessions ('session deploy run').

  runtm-api deployments list --state ready
  runtm-api deployments get <deployment_id>
  runtm-api deployments logs <deployment_id> --type runtime --lines 100
  runtm-api deployments destroy <deployment_id> --yes

Scopes: deployments:read for list/get/logs, deployments:delete for destroy.`,
	}
	cmd.AddCommand(
		newDeploymentsList(rt),
		newDeploymentsGet(rt),
		newDeploymentsLogs(rt),
		newDeploymentsDestroy(rt),
	)
	return cmd
}

const deploymentsPath = "/v0/deployments"

func deploymentPath(id string) string {
	return deploymentsPath + "/" + url.PathEscape(id)
}

func newDeploymentsList(rt *Runtime) *cobra.Command {
	var (
		state  string
		limit  int
		offset int
	)
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List deployments (GET /api/v0/deployments)",
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			q := url.Values{}
			if state != "" {
				q.Set("state", state)
			}
			if limit > 0 {
				q.Set("limit", strconv.Itoa(limit))
			}
			if offset > 0 {
				q.Set("offset", strconv.Itoa(offset))
			}
			resp, err := c.Get(deploymentsPath, q)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&state, "state", "", "Filter by state: queued, building, deploying, ready, failed, destroyed")
	cmd.Flags().IntVar(&limit, "limit", 0, "Results per page (1-100)")
	cmd.Flags().IntVar(&offset, "offset", 0, "Pagination offset")
	return cmd
}

func newDeploymentsGet(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "get <deployment_id>",
		Short: "Get one deployment (GET /api/v0/deployments/{id})",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			resp, err := c.Get(deploymentPath(args[0]), nil)
			return runJSON(rt, resp, err)
		},
	}
}

func newDeploymentsLogs(rt *Runtime) *cobra.Command {
	var (
		logType string
		lines   int
		search  string
	)
	cmd := &cobra.Command{
		Use:   "logs <deployment_id>",
		Short: "Fetch stored deployment logs (GET /api/v0/deployments/{id}/logs)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			q := url.Values{}
			if logType != "" {
				q.Set("type", logType)
			}
			if lines > 0 {
				q.Set("lines", strconv.Itoa(lines))
			}
			if search != "" {
				q.Set("search", search)
			}
			resp, err := c.Get(deploymentPath(args[0])+"/logs", q)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&logType, "type", "", "Log type: build, deploy, or runtime (default: all)")
	cmd.Flags().IntVar(&lines, "lines", 0, "Number of lines to return (default 20, max 1000)")
	cmd.Flags().StringVar(&search, "search", "", "Substring filter on log content")
	return cmd
}

func newDeploymentsDestroy(rt *Runtime) *cobra.Command {
	var yes bool
	cmd := &cobra.Command{
		Use:   "destroy <deployment_id>",
		Short: "Tear down a deployment (DELETE /api/v0/deployments/{id})",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if !yes {
				rt.WriteObject(map[string]any{
					"error": "Destructive operation requires --yes to confirm.",
					"hint":  "Destroying takes the deployment's URL offline. Pass --yes when you are sure.",
				})
				return errSilent
			}
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			resp, err := c.Delete(deploymentPath(args[0]))
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().BoolVar(&yes, "yes", false, "Confirm destruction")
	return cmd
}
