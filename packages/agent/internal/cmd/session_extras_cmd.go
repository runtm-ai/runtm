// Package cmd: extended session subcommands beyond the core CRUD in session.go.
//
// Adds the canonical /api/sessions/{id}/* operations needed for full feature
// parity with the dashboard: prompt history, live events, per-session
// instructions, visibility, collaborators, workspace-state, heartbeat, and
// the extended file / env operations (search, mkdir, rename, delete, detect).
//
// Wired into the existing `runtm session` command in root.go via
// `AddSessionExtras(sessionCmd, rt)`.
package cmd

import (
	"fmt"
	"net/url"
	"strconv"

	"github.com/spf13/cobra"
)

// AddSessionExtras attaches the new session subcommands to the existing
// `runtm session` tree.
func AddSessionExtras(sessionCmd *cobra.Command, rt *Runtime) {
	sessionCmd.AddCommand(
		newSessionHistory(rt),
		newSessionEvents(rt),
		newSessionPromptCancel(rt),
		newSessionPromptRewind(rt),
		newSessionVisibility(rt),
		newSessionInstructions(rt),
		newSessionCollaborators(rt),
		newSessionWorkspaceState(rt),
		newSessionHeartbeat(rt),
		newSessionRunServer(rt),
	)
	// Extend the existing `runtm session file` and `runtm session env` trees
	// in place. Both functions return the existing command from session.go via
	// the registered subcommand list -- see attach helpers below.
	attachFileExtras(sessionCmd, rt)
	attachEnvExtras(sessionCmd, rt)
}

// --- prompt history -------------------------------------------------------

func newSessionHistory(rt *Runtime) *cobra.Command {
	var limit int
	cmd := &cobra.Command{
		Use:   "history <session_id>",
		Short: "List prompt history for a session (GET /api/sessions/{id}/history)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			q := url.Values{}
			if limit > 0 {
				q.Set("limit", strconv.Itoa(limit))
			}
			resp, err := c.Get("/sessions/"+url.PathEscape(args[0])+"/history", q)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().IntVar(&limit, "limit", 0, "Max prompts to return (default server-side)")
	return cmd
}

// --- live events SSE ------------------------------------------------------

func newSessionEvents(rt *Runtime) *cobra.Command {
	var since int
	cmd := &cobra.Command{
		Use:   "events <session_id>",
		Short: "Stream session events (GET /api/sessions/{id}/events) as SSE -> JSON lines",
		Long: `Streams the live agent event bus for a session. Each stdout line is a JSON
envelope: {"event": "<type>", "data": <payload>}. Useful for following an
in-progress prompt without re-issuing POST /prompt.

Pass --since to replay events from a specific sequence number.`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			path := "/sessions/" + url.PathEscape(args[0]) + "/events"
			if since > 0 {
				path += "?since=" + strconv.Itoa(since)
			}
			return c.StreamSSEGet(path, rt.Stdout)
		},
	}
	cmd.Flags().IntVar(&since, "since", 0, "Replay events with seq > this value")
	return cmd
}

// --- prompt control -------------------------------------------------------

func newSessionPromptCancel(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "prompt-cancel <session_id>",
		Short: "Cancel the running prompt (POST /api/sessions/{id}/prompt/cancel)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			resp, err := c.PostJSON("/sessions/"+url.PathEscape(args[0])+"/prompt/cancel", map[string]any{})
			return runJSON(rt, resp, err)
		},
	}
}

func newSessionPromptRewind(rt *Runtime) *cobra.Command {
	var toIndex int
	cmd := &cobra.Command{
		Use:   "prompt-rewind <session_id>",
		Short: "Rewind history to a previous prompt (POST /api/sessions/{id}/prompt/rewind)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			body := map[string]any{}
			if cmd.Flags().Changed("to-index") {
				body["to_index"] = toIndex
			}
			resp, err := c.PostJSON("/sessions/"+url.PathEscape(args[0])+"/prompt/rewind", body)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().IntVar(&toIndex, "to-index", 0, "Rewind to prompt index (0-based)")
	return cmd
}

// --- visibility -----------------------------------------------------------

func newSessionVisibility(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "visibility <session_id> <private|team>",
		Short: "Change session visibility (POST /api/sessions/{id}/visibility)",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			body := map[string]any{"visibility": args[1]}
			resp, err := c.PostJSON("/sessions/"+url.PathEscape(args[0])+"/visibility", body)
			return runJSON(rt, resp, err)
		},
	}
}

// --- per-session instructions --------------------------------------------

func newSessionInstructions(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "instructions",
		Short: "Read and update per-session agent instructions (separate from user/org)",
	}
	cmd.AddCommand(
		&cobra.Command{
			Use:   "get <session_id>",
			Short: "GET /api/sessions/{id}/instructions",
			Args:  cobra.ExactArgs(1),
			RunE: func(cmd *cobra.Command, args []string) error {
				c, _, err := rt.Client()
				if err != nil {
					return err
				}
				resp, err := c.Get("/sessions/"+url.PathEscape(args[0])+"/instructions", nil)
				return runJSON(rt, resp, err)
			},
		},
		newSessionInstructionsSet(rt),
	)
	return cmd
}

func newSessionInstructionsSet(rt *Runtime) *cobra.Command {
	var (
		text  string
		clear bool
	)
	cmd := &cobra.Command{
		Use:   "set <session_id>",
		Short: "PUT /api/sessions/{id}/instructions",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if text == "" && !clear {
				return fmt.Errorf("pass --text or --clear")
			}
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			body := map[string]any{}
			if clear {
				body["instructions"] = nil
			} else {
				body["instructions"] = text
			}
			resp, err := c.PutJSON("/sessions/"+url.PathEscape(args[0])+"/instructions", body)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVarP(&text, "text", "t", "", "New instructions content")
	cmd.Flags().BoolVar(&clear, "clear", false, "Clear instructions (set null)")
	return cmd
}

// --- collaborators --------------------------------------------------------

func newSessionCollaborators(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "collaborators <session_id>",
		Short: "List collaborators currently connected (GET /api/sessions/{id}/collaborators)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			resp, err := c.Get("/sessions/"+url.PathEscape(args[0])+"/collaborators", nil)
			return runJSON(rt, resp, err)
		},
	}
}

// --- workspace-state ------------------------------------------------------

func newSessionWorkspaceState(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "workspace-state <session_id>",
		Short: "Snapshot of dirty files / open tabs / preview state (GET /api/sessions/{id}/workspace-state)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			resp, err := c.Get("/sessions/"+url.PathEscape(args[0])+"/workspace-state", nil)
			return runJSON(rt, resp, err)
		},
	}
}

// --- heartbeat ------------------------------------------------------------

func newSessionHeartbeat(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "heartbeat <session_id>",
		Short: "Bump the idle timer (POST /api/sessions/{id}/heartbeat)",
		Long: `Keeps a session alive by resetting its idle countdown. Auto-pause kicks in
after ~20 minutes of no heartbeat. Useful for long-running background agents
that want to defer auto-pause.`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			resp, err := c.PostJSON("/sessions/"+url.PathEscape(args[0])+"/heartbeat", map[string]any{})
			return runJSON(rt, resp, err)
		},
	}
}

// --- run-server -----------------------------------------------------------

func newSessionRunServer(rt *Runtime) *cobra.Command {
	var (
		port    int
		command string
	)
	cmd := &cobra.Command{
		Use:   "run-server <session_id>",
		Short: "Start a dev server in the sandbox (POST /api/sessions/{id}/run-server)",
		Long: `Starts (or restarts) the dev server. The server's port is exposed as a
preview URL on the session detail. Without --command, the template's default
start_cmd is used.`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			body := map[string]any{}
			if port > 0 {
				body["port"] = port
			}
			if command != "" {
				body["command"] = command
			}
			resp, err := c.PostJSON("/sessions/"+url.PathEscape(args[0])+"/run-server", body)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().IntVar(&port, "port", 0, "Override the port")
	cmd.Flags().StringVar(&command, "command", "", "Override the start command")
	return cmd
}

// --- file advanced ops attach helper -------------------------------------

func attachFileExtras(sessionCmd *cobra.Command, rt *Runtime) {
	fileCmd, _, _ := sessionCmd.Find([]string{"file"})
	if fileCmd == nil {
		return
	}
	fileCmd.AddCommand(
		newSessionFileSearch(rt),
		newSessionFileMkdir(rt),
		newSessionFileRename(rt),
		newSessionFileDelete(rt),
	)
}

func newSessionFileSearch(rt *Runtime) *cobra.Command {
	var (
		path  string
		query string
	)
	cmd := &cobra.Command{
		Use:   "search <session_id>",
		Short: "Search files in the sandbox (GET /api/sessions/{id}/files/search)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if query == "" {
				return fmt.Errorf("--query is required")
			}
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			q := url.Values{}
			q.Set("query", query)
			if path != "" {
				q.Set("path", path)
			}
			resp, err := c.Get("/sessions/"+url.PathEscape(args[0])+"/files/search", q)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVarP(&query, "query", "q", "", "Search pattern (required)")
	cmd.Flags().StringVar(&path, "path", "", "Directory to search under (default workspace root)")
	_ = cmd.MarkFlagRequired("query")
	return cmd
}

func newSessionFileMkdir(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "mkdir <session_id> <path>",
		Short: "Create a directory (POST /api/sessions/{id}/file/mkdir)",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			body := map[string]any{"path": args[1]}
			resp, err := c.PostJSON("/sessions/"+url.PathEscape(args[0])+"/file/mkdir", body)
			return runJSON(rt, resp, err)
		},
	}
}

func newSessionFileRename(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "rename <session_id> <old_path> <new_path>",
		Short: "Move or rename a file (POST /api/sessions/{id}/file/rename)",
		Args:  cobra.ExactArgs(3),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			body := map[string]any{"old_path": args[1], "new_path": args[2]}
			resp, err := c.PostJSON("/sessions/"+url.PathEscape(args[0])+"/file/rename", body)
			return runJSON(rt, resp, err)
		},
	}
}

func newSessionFileDelete(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "delete <session_id> <path>",
		Short: "Delete a file or directory (DELETE /api/sessions/{id}/file)",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			// DELETE with body via PatchJSON-style helper isn't available;
			// the canonical client only does query/body via DELETE. We use a
			// POST-like body delivered through the standard JSON body path.
			// The backend accepts DELETE with JSON body for this endpoint.
			body := map[string]any{"path": args[1]}
			resp, err := c.DeleteJSON("/sessions/"+url.PathEscape(args[0])+"/file", body)
			return runJSON(rt, resp, err)
		},
	}
}

// --- env detect / detected ------------------------------------------------

func attachEnvExtras(sessionCmd *cobra.Command, rt *Runtime) {
	envCmd, _, _ := sessionCmd.Find([]string{"env"})
	if envCmd == nil {
		return
	}
	envCmd.AddCommand(
		newSessionEnvDetected(rt),
		newSessionEnvDetect(rt),
	)
}

func newSessionEnvDetected(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "detected <session_id>",
		Short: "List env vars detected in the workspace (GET /api/sessions/{id}/detected-env-vars)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			resp, err := c.Get("/sessions/"+url.PathEscape(args[0])+"/detected-env-vars", nil)
			return runJSON(rt, resp, err)
		},
	}
}

func newSessionEnvDetect(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "detect <session_id>",
		Short: "Re-scan the workspace for referenced env vars (POST /api/sessions/{id}/detect-env-vars)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			resp, err := c.PostJSON("/sessions/"+url.PathEscape(args[0])+"/detect-env-vars", map[string]any{})
			return runJSON(rt, resp, err)
		},
	}
}
