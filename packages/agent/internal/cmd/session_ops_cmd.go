package cmd

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strconv"

	"github.com/spf13/cobra"
)

// AddSessionOps attaches the workflow commands that round out the session
// surface: search, run grades, approvals, capability loading, and binary file
// transfer.
//
// Routes:
//
//	GET  /api/sessions:search                              sessions:read
//	GET  /api/sessions/{id}/grade                          activity:read
//	GET  /api/sessions/{id}/approvals                      sessions:read
//	POST /api/sessions/{id}/approvals/{approval_id}/resolve sessions:write
//	POST /api/sessions/{id}/skills                         sessions:write
//	POST /api/sessions/{id}/mcps                           sessions:write
//	GET  /api/sessions/{id}/tools                          sessions:read
//	POST /api/sessions/{id}/tools                          sessions:write
//	POST /api/sessions/{id}/file/upload                    sessions:write
//	GET  /api/sessions/{id}/file/download                  sessions:read
func AddSessionOps(sessionCmd *cobra.Command, rt *Runtime) {
	sessionCmd.AddCommand(
		newSessionSearch(rt),
		newSessionGrade(rt),
		newSessionApprovals(rt),
		newSessionLoadSkills(rt),
		newSessionLoadMcps(rt),
		newSessionLoadTools(rt),
		newSessionTools(rt),
	)
	if fileCmd, _, _ := sessionCmd.Find([]string{"file"}); fileCmd != nil && fileCmd != sessionCmd {
		fileCmd.AddCommand(
			newSessionFileUpload(rt),
			newSessionFileDownload(rt),
		)
	}
}

// --- search ----------------------------------------------------------------

func newSessionSearch(rt *Runtime) *cobra.Command {
	var (
		query              string
		agents             []string
		models             []string
		templates          []string
		sources            []string
		creators           []string
		createdAfter       string
		createdBefore      string
		lastActivityAfter  string
		lastActivityBefore string
		excludeSources     string
		excludeStates      string
		includeDestroyed   bool
		teamMode           bool
		limit              int
		offset             int
	)
	cmd := &cobra.Command{
		Use:   "search",
		Short: "Search sessions with filters (GET /api/sessions:search)",
		Long: `Fuzzy-search sessions by name/prompt text and filter by agent, model,
template, source, creator, and time windows. 'session list' only pages;
this is the way to find a session when you know something about it.

  runtm-api session search -q "outbound lists"
  runtm-api session search --agent codex --created-after 2026-07-01
  runtm-api session search --template gtm-machine --team-mode`,
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			q := url.Values{}
			if query != "" {
				q.Set("q", query)
			}
			for _, v := range agents {
				q.Add("agent", v)
			}
			for _, v := range models {
				q.Add("model", v)
			}
			for _, v := range templates {
				q.Add("template", v)
			}
			for _, v := range sources {
				q.Add("source", v)
			}
			for _, v := range creators {
				q.Add("creator_id", v)
			}
			if createdAfter != "" {
				q.Set("created_after", createdAfter)
			}
			if createdBefore != "" {
				q.Set("created_before", createdBefore)
			}
			if lastActivityAfter != "" {
				q.Set("last_activity_after", lastActivityAfter)
			}
			if lastActivityBefore != "" {
				q.Set("last_activity_before", lastActivityBefore)
			}
			if excludeSources != "" {
				q.Set("exclude_sources", excludeSources)
			}
			if excludeStates != "" {
				q.Set("exclude_states", excludeStates)
			}
			if includeDestroyed {
				q.Set("include_destroyed", "true")
			}
			if teamMode {
				q.Set("team_mode", "true")
			}
			if limit > 0 {
				q.Set("limit", strconv.Itoa(limit))
			}
			if offset > 0 {
				q.Set("offset", strconv.Itoa(offset))
			}
			resp, err := c.Get("/sessions:search", q)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVarP(&query, "query", "q", "", "Fuzzy text match on session name / prompt")
	cmd.Flags().StringArrayVar(&agents, "agent", nil, "Filter by coding agent (repeatable)")
	cmd.Flags().StringArrayVar(&models, "model", nil, "Filter by model (repeatable)")
	cmd.Flags().StringArrayVar(&templates, "template", nil, "Filter by template slug (repeatable)")
	cmd.Flags().StringArrayVar(&sources, "source", nil, "Filter by source, e.g. web_ui, agents, slack, schedule (repeatable)")
	cmd.Flags().StringArrayVar(&creators, "creator", nil, "Filter by creator user id (repeatable)")
	cmd.Flags().StringVar(&createdAfter, "created-after", "", "Created after (ISO date/datetime)")
	cmd.Flags().StringVar(&createdBefore, "created-before", "", "Created before (ISO date/datetime)")
	cmd.Flags().StringVar(&lastActivityAfter, "last-activity-after", "", "Last activity after (ISO date/datetime)")
	cmd.Flags().StringVar(&lastActivityBefore, "last-activity-before", "", "Last activity before (ISO date/datetime)")
	cmd.Flags().StringVar(&excludeSources, "exclude-sources", "", "Comma-separated sources to exclude")
	cmd.Flags().StringVar(&excludeStates, "exclude-states", "", "Comma-separated states to exclude")
	cmd.Flags().BoolVar(&includeDestroyed, "include-destroyed", false, "Include destroyed sessions")
	cmd.Flags().BoolVar(&teamMode, "team-mode", false, "Search team-shared sessions (org context)")
	cmd.Flags().IntVar(&limit, "limit", 0, "Results per page (1-100)")
	cmd.Flags().IntVar(&offset, "offset", 0, "Pagination offset")
	return cmd
}

// --- grade -------------------------------------------------------------------

func newSessionGrade(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "grade <session_id>",
		Short: "The run's evaluation verdict (GET /api/sessions/{id}/grade)",
		Long: `Returns the latest performance-evaluation verdict for a run:
{"graded": true, "success": ..., "reason": ..., "task": ...} once the grader
has scored it against the agent's evaluator_criteria, or {"graded": false}
when the run has not been evaluated (no rubric on the agent, or grading has
not run yet). Set the rubric with 'agents update <id> --evaluator-criteria';
see the aggregate with 'agents scorecard'.`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			resp, err := c.Get("/sessions/"+url.PathEscape(args[0])+"/grade", nil)
			return runJSON(rt, resp, err)
		},
	}
}

// --- approvals ---------------------------------------------------------------

func newSessionApprovals(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "approvals",
		Short: "List and resolve a session's approval gates",
		Long: `Autopilot runs can pause on an approval gate (a deploy, a spend, a
guarded action) and wait for a human decision. Without these commands an
unattended run that hits a gate simply stalls. List the gates, then resolve:

  runtm-api session approvals list <session_id>
  runtm-api session approvals resolve <session_id> <approval_id> --approve
  runtm-api session approvals resolve <session_id> <approval_id> --reject --note "not this repo"

Resolution is role-gated server-side: org admins and owners always can;
otherwise the resolver must match the approval's required_role or belong to
its required_team_id.`,
	}
	cmd.AddCommand(
		newSessionApprovalsList(rt),
		newSessionApprovalsResolve(rt),
	)
	return cmd
}

func newSessionApprovalsList(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "list <session_id>",
		Short: "List approval requests, newest first (GET /api/sessions/{id}/approvals)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			resp, err := c.Get("/sessions/"+url.PathEscape(args[0])+"/approvals", nil)
			return runJSON(rt, resp, err)
		},
	}
}

func newSessionApprovalsResolve(rt *Runtime) *cobra.Command {
	var (
		approve bool
		reject  bool
		note    string
	)
	cmd := &cobra.Command{
		Use:   "resolve <session_id> <approval_id>",
		Short: "Approve or reject a pending approval (POST .../approvals/{id}/resolve)",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			if approve == reject {
				return fmt.Errorf("pass exactly one of --approve or --reject")
			}
			action := "approve"
			if reject {
				action = "reject"
			}
			body := map[string]any{"action": action}
			if note != "" {
				body["note"] = note
			}
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			path := "/sessions/" + url.PathEscape(args[0]) + "/approvals/" + url.PathEscape(args[1]) + "/resolve"
			resp, err := c.PostJSON(path, body)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().BoolVar(&approve, "approve", false, "Approve the request")
	cmd.Flags().BoolVar(&reject, "reject", false, "Reject the request")
	cmd.Flags().StringVar(&note, "note", "", "Optional resolution note (max 512 chars)")
	return cmd
}

// --- capability loading -------------------------------------------------------

func newSessionLoadSkills(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "load-skills <session_id> <skill_id> [skill_id...]",
		Short: "Load org skills into a RUNNING session (POST /api/sessions/{id}/skills)",
		Long: `Hot-loads skills into a running sandbox without a template rebuild.
IDs are agent-directive ids (from 'runtm-api skills list'), not names.
The response reports what loaded, what was skipped, and any unmet
integration requirements.`,
		Args: cobra.MinimumNArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			body := map[string]any{"skill_ids": args[1:]}
			resp, err := c.PostJSON("/sessions/"+url.PathEscape(args[0])+"/skills", body)
			return runJSON(rt, resp, err)
		},
	}
}

func newSessionLoadMcps(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "load-mcps <session_id> <mcp_id> [mcp_id...]",
		Short: "Load MCP servers into a RUNNING session (POST /api/sessions/{id}/mcps)",
		Long: `Hot-loads MCP servers into a running sandbox. IDs are agent-directive ids
(from 'runtm-api mcp list'). Servers whose connections still need auth are
reported under needs_auth.`,
		Args: cobra.MinimumNArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			body := map[string]any{"mcp_ids": args[1:]}
			resp, err := c.PostJSON("/sessions/"+url.PathEscape(args[0])+"/mcps", body)
			return runJSON(rt, resp, err)
		},
	}
}

func newSessionLoadTools(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "load-tools <session_id> <provider_slug> [provider_slug...]",
		Short: "Load tools into a RUNNING session (POST /api/sessions/{id}/tools)",
		Long: `Hot-loads tools (knowledge integrations) into a running sandbox. Arguments
are provider slugs like 'notion' or 'bigquery' (from 'runtm-api tools list'),
not directive ids. Providers whose credentials are missing are reported
under needs_auth.`,
		Args: cobra.MinimumNArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			body := map[string]any{"provider_slugs": args[1:]}
			resp, err := c.PostJSON("/sessions/"+url.PathEscape(args[0])+"/tools", body)
			return runJSON(rt, resp, err)
		},
	}
}

func newSessionTools(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "tools <session_id>",
		Short: "List the tools loaded into a session (GET /api/sessions/{id}/tools)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			resp, err := c.Get("/sessions/"+url.PathEscape(args[0])+"/tools", nil)
			return runJSON(rt, resp, err)
		},
	}
}

// --- binary file transfer -----------------------------------------------------

func newSessionFileUpload(rt *Runtime) *cobra.Command {
	var remotePath string
	cmd := &cobra.Command{
		Use:   "upload <session_id> <local_path> [remote_path]",
		Short: "Upload a local file into the sandbox (POST /api/sessions/{id}/file/upload)",
		Long: `Uploads a local file (binary-safe, base64 over the wire). The remote path
defaults to /home/user/<basename>. For plain text 'session file write' also
works; this is the way to move CSVs, archives, and other binary artifacts in.`,
		Args: cobra.RangeArgs(2, 3),
		RunE: func(cmd *cobra.Command, args []string) error {
			local := args[1]
			remote := remotePath
			if len(args) == 3 {
				remote = args[2]
			}
			if remote == "" {
				remote = "/home/user/" + filepath.Base(local)
			}
			data, err := os.ReadFile(local) // #nosec G304 -- the named file is the command's argument
			if err != nil {
				return fmt.Errorf("read %s: %w", local, err)
			}
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			body := map[string]any{
				"path":           remote,
				"content_base64": base64.StdEncoding.EncodeToString(data),
			}
			resp, err := c.PostJSON("/sessions/"+url.PathEscape(args[0])+"/file/upload", body)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&remotePath, "path", "", "Destination path in the sandbox (default /home/user/<basename>)")
	return cmd
}

func newSessionFileDownload(rt *Runtime) *cobra.Command {
	var outPath string
	cmd := &cobra.Command{
		Use:   "download <session_id> <remote_path> [local_path]",
		Short: "Download a file or directory from the sandbox (GET /api/sessions/{id}/file/download)",
		Long: `Downloads a sandbox file to the local disk (binary-safe). Directories
arrive as a .tar.gz archive. Capped at 50 MB. Prints a JSON summary; the
bytes go to the local path (default: the remote basename in the current
directory).`,
		Args: cobra.RangeArgs(2, 3),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			q := url.Values{}
			q.Set("path", args[1])
			resp, err := c.Get("/sessions/"+url.PathEscape(args[0])+"/file/download", q)
			if err != nil {
				return err
			}
			var out struct {
				Path          string `json:"path"`
				Filename      string `json:"filename"`
				IsDir         bool   `json:"is_dir"`
				ContentBase64 string `json:"content_base64"`
				Size          int64  `json:"size"`
				MimeType      string `json:"mime_type"`
			}
			if jerr := json.Unmarshal(resp, &out); jerr != nil {
				return fmt.Errorf("could not parse download response: %w", jerr)
			}
			data, derr := base64.StdEncoding.DecodeString(out.ContentBase64)
			if derr != nil {
				return fmt.Errorf("could not decode file content: %w", derr)
			}
			local := outPath
			if len(args) == 3 {
				local = args[2]
			}
			if local == "" {
				local = out.Filename
			}
			if err := os.WriteFile(local, data, 0o600); err != nil {
				return fmt.Errorf("write %s: %w", local, err)
			}
			rt.WriteObject(map[string]any{
				"remote_path": out.Path,
				"local_path":  local,
				"size":        out.Size,
				"is_dir":      out.IsDir,
				"mime_type":   out.MimeType,
				"success":     true,
			})
			return nil
		},
	}
	cmd.Flags().StringVar(&outPath, "out", "", "Local destination path (default: the remote basename)")
	return cmd
}
