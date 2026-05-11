package cmd

import (
	"net/url"

	"github.com/spf13/cobra"
)

// AddSessionDeploy attaches `runtm session deploy <id> ...` -- the canonical
// session-deploy flow distinct from the OSS pip CLI deploy.
//
// Routes (all canonical, dual-auth):
//
//	GET  /api/sessions/{id}/deploy/info        deployments:read
//	POST /api/sessions/{id}/deploy/scaffold    deployments:write
//	POST /api/sessions/{id}/deploy/validate    deployments:write
//	POST /api/sessions/{id}/deploy/preflight   deployments:write
//	POST /api/sessions/{id}/deploy/run         deployments:write (returns SSE)
func AddSessionDeploy(sessionCmd *cobra.Command, rt *Runtime) {
	deploy := &cobra.Command{
		Use:   "deploy <session_id>",
		Short: "Deploy a session: scaffold manifest, validate, preflight, run",
		Long: `Cloud session deploy lives at /api/sessions/{id}/deploy/*. Walk a session
through scaffold -> validate -> preflight -> run to build and ship the
sandbox's contents as a Fly-backed app.

This is the session-deploy surface, separate from the pip CLI's project-root
'runtm deploy' which uploads a manifest + artifact from the local filesystem.`,
	}
	deploy.AddCommand(
		newSessionDeployInfo(rt),
		newSessionDeployScaffold(rt),
		newSessionDeployValidate(rt),
		newSessionDeployPreflight(rt),
		newSessionDeployRun(rt),
	)
	sessionCmd.AddCommand(deploy)
}

func newSessionDeployInfo(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "info <session_id>",
		Short: "Inspect deploy readiness (GET /api/sessions/{id}/deploy/info)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			resp, err := c.Get("/sessions/"+url.PathEscape(args[0])+"/deploy/info", nil)
			return runJSON(rt, resp, err)
		},
	}
}

func newSessionDeployScaffold(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "scaffold <session_id>",
		Short: "Generate runtm.yaml from the session workspace (POST /api/sessions/{id}/deploy/scaffold)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			resp, err := c.PostJSON("/sessions/"+url.PathEscape(args[0])+"/deploy/scaffold", map[string]any{})
			return runJSON(rt, resp, err)
		},
	}
}

func newSessionDeployValidate(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "validate <session_id>",
		Short: "Validate the session's runtm.yaml (POST /api/sessions/{id}/deploy/validate)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			resp, err := c.PostJSON("/sessions/"+url.PathEscape(args[0])+"/deploy/validate", map[string]any{})
			return runJSON(rt, resp, err)
		},
	}
}

func newSessionDeployPreflight(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "preflight <session_id>",
		Short: "Run pre-deploy checks (POST /api/sessions/{id}/deploy/preflight)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			resp, err := c.PostJSON("/sessions/"+url.PathEscape(args[0])+"/deploy/preflight", map[string]any{})
			return runJSON(rt, resp, err)
		},
	}
}

func newSessionDeployRun(rt *Runtime) *cobra.Command {
	var (
		newDeploy bool
		tier      string
	)
	cmd := &cobra.Command{
		Use:   "run <session_id>",
		Short: "Build + ship the session as a deployment (SSE stream)",
		Long: `Streams build / deploy progress as Server-Sent Events. Each stdout line is
a JSON envelope: {"event": "<type>", "data": <payload>}. The stream ends with
event "done" once the deployment finishes (or errors).`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			body := map[string]any{}
			if newDeploy {
				body["new"] = true
			}
			if tier != "" {
				body["tier"] = tier
			}
			return c.StreamSSE("/sessions/"+url.PathEscape(args[0])+"/deploy/run", body, rt.Stdout)
		},
	}
	cmd.Flags().BoolVar(&newDeploy, "new", false, "Force a fresh deployment instead of redeploying")
	cmd.Flags().StringVar(&tier, "tier", "", "Override machine tier (starter, standard, performance)")
	return cmd
}
