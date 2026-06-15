package cmd

import (
	"fmt"
	"net/url"
	"strconv"

	"github.com/spf13/cobra"
)

// NewSessionCommand returns the `runtm session` subcommand tree.
//
// Endpoint strategy (per the programmatic API plan):
//   - CRUD / lifecycle: canonical /api/sessions/* dual-auth routes.
//   - Fire-and-forget agent launch: /api/v0/sessions/launch (the plan keeps
//     v0 launch as the recommended agent entry point).
//   - Status polling: /api/v0/sessions/{id} (v0 includes last_prompt fields
//     designed for poll-driven workflows).
//   - Prompt streaming: /api/v0/sessions/{id}/prompt (synchronous SSE; the
//     canonical equivalent is POST + separate /events GET which is a worse
//     CLI UX for AI agents).
func NewSessionCommand(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "session",
		Short: "Create, prompt, and manage cloud sessions",
		Long: `Sessions are cloud sandboxes where coding agents run.

Common flows:
  1. List org templates -> launch a session against one with a prompt
  2. Create a blank session -> send prompts iteratively
  3. Open a PR with the session's changes via 'runtm session git'

See https://docs.runtm.com/cloud-api/sessions for endpoint details.`,
	}
	cmd.AddCommand(
		newSessionCreate(rt),
		newSessionLaunch(rt),
		newSessionList(rt),
		newSessionGet(rt),
		newSessionStatus(rt),
		newSessionPrompt(rt),
		newSessionDestroy(rt),
		newSessionGit(rt),
		newSessionPause(rt),
		newSessionResume(rt),
		newSessionRename(rt),
		newSessionFile(rt),
		newSessionEnv(rt),
	)
	return cmd
}

// --- create (CANONICAL: /api/sessions/) ----------------------------------

func newSessionCreate(rt *Runtime) *cobra.Command {
	var (
		agent        string
		template     string
		templateID   string
		templateArgs map[string]string
		mode         string
		onComplete   string
		ttlMinutes   int
		repoFull     string
		repoSize     int
		source       string
	)
	cmd := &cobra.Command{
		Use:   "create",
		Short: "Create a session (POST /api/sessions/)",
		Long: `Creates a session backed by an E2B sandbox. With no flags the session boots
a blank sandbox.

Boot sources (highest precedence first):
  --template-id <uuid>   Launch from a pre-built org template (use
                         'runtm-api template list' to find the id). Pass
                         per-session args with --template-args key=value.
  --repo owner/repo      Clone a GitHub repo into a fresh sandbox.
  --template <scaffold>  Scaffold a starter project (web-app, backend-service,
                         static-site).

See https://docs.runtm.com/cloud-api/sessions/create for the full schema.`,
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}

			body := map[string]any{
				"source": fallback(source, "api"),
			}
			if agent != "" {
				body["agent"] = agent
			}
			if template != "" {
				body["template"] = template
			}
			if templateID != "" {
				body["template_id"] = templateID
			}
			if len(templateArgs) > 0 {
				args := make(map[string]any, len(templateArgs))
				for k, v := range templateArgs {
					args[k] = v
				}
				body["template_args"] = args
			}
			if mode != "" {
				body["mode"] = mode
			}
			if onComplete != "" {
				body["on_complete"] = onComplete
			}
			if ttlMinutes > 0 {
				body["ttl_minutes"] = ttlMinutes
			}
			if repoFull != "" {
				repo := map[string]any{"full_name": repoFull}
				if repoSize > 0 {
					repo["size_kb"] = repoSize
				}
				body["github_repo"] = repo
			}

			resp, err := c.PostJSON("/sessions/", body)
			if err != nil {
				return err
			}
			rt.WriteJSON(resp)
			return nil
		},
	}
	cmd.Flags().StringVar(&agent, "agent", "", "Coding agent (claude-code, codex, opencode, github-copilot, cursor-cli, devin-cli, gemini-cli)")
	cmd.Flags().StringVar(&template, "template", "", "Project template scaffold (web-app, backend-service, static-site)")
	cmd.Flags().StringVar(&templateID, "template-id", "", "Org template UUID to boot from (takes precedence over --template and --repo)")
	cmd.Flags().StringToStringVar(&templateArgs, "template-args", nil, "Org template session args as key=value pairs (only with --template-id)")
	cmd.Flags().StringVar(&mode, "mode", "", "Session mode: autopilot (default) or interactive")
	cmd.Flags().StringVar(&onComplete, "on-complete", "", "Lifecycle action after prompts complete: pause, destroy, keep_alive")
	cmd.Flags().IntVar(&ttlMinutes, "ttl-minutes", 0, "Max session lifetime in minutes (1-1440)")
	cmd.Flags().StringVar(&repoFull, "repo", "", "GitHub repo in owner/repo form")
	cmd.Flags().IntVar(&repoSize, "repo-size-kb", 0, "Repo size in KB (used for tier selection)")
	cmd.Flags().StringVar(&source, "source", "api", "Telemetry source label")
	return cmd
}

// --- launch (LEGACY v0: kept per plan as the agent fire-and-forget entry) -

func newSessionLaunch(rt *Runtime) *cobra.Command {
	var (
		prompt        string
		agent         string
		template      string
		model         string
		mode          string
		planMode      bool
		onComplete    string
		ttlMinutes    int
		promptTimeout int
		repoFull      string
		repoSize      int
	)
	cmd := &cobra.Command{
		Use:   "launch",
		Short: "Create a session and fire a prompt in one call (POST /api/v0/sessions/launch)",
		Long: `The canonical fire-and-forget entry point for programmatic agents
(per the cloud-api docs). Returns immediately with the session ID; poll
'runtm session status <id>' until last_prompt.status is completed/error/timed_out.`,
		RunE: func(cmd *cobra.Command, args []string) error {
			if prompt == "" {
				return fmt.Errorf("--prompt is required")
			}
			c, _, err := rt.Client()
			if err != nil {
				return err
			}

			body := map[string]any{"prompt": prompt}
			if agent != "" {
				body["agent"] = agent
			}
			if template != "" {
				body["template"] = template
			}
			if model != "" {
				body["model"] = model
			}
			if mode != "" {
				body["mode"] = mode
			}
			if planMode {
				body["plan_mode"] = true
			}
			if onComplete != "" {
				body["on_complete"] = onComplete
			}
			if ttlMinutes > 0 {
				body["ttl_minutes"] = ttlMinutes
			}
			if promptTimeout > 0 {
				body["prompt_timeout_minutes"] = promptTimeout
			}
			if repoFull != "" {
				repo := map[string]any{"full_name": repoFull}
				if repoSize > 0 {
					repo["size_kb"] = repoSize
				}
				body["github_repo"] = repo
			}

			resp, err := c.PostJSON("/v0/sessions/launch", body)
			if err != nil {
				return err
			}
			rt.WriteJSON(resp)
			return nil
		},
	}
	cmd.Flags().StringVarP(&prompt, "prompt", "p", "", "Prompt/task for the agent (required)")
	cmd.Flags().StringVar(&agent, "agent", "", "Coding agent (claude-code, codex, github-copilot, opencode)")
	cmd.Flags().StringVar(&template, "template", "", "Project template (web-app, backend-service, static-site)")
	cmd.Flags().StringVar(&model, "model", "", "Model override: sonnet, opus, haiku, opusplan")
	cmd.Flags().StringVar(&mode, "mode", "", "Session mode: autopilot or interactive")
	cmd.Flags().BoolVar(&planMode, "plan-mode", false, "Read-only plan mode (no changes made)")
	cmd.Flags().StringVar(&onComplete, "on-complete", "", "Lifecycle after prompt: pause, destroy, keep_alive")
	cmd.Flags().IntVar(&ttlMinutes, "ttl-minutes", 0, "Max session lifetime in minutes")
	cmd.Flags().IntVar(&promptTimeout, "prompt-timeout-minutes", 0, "Max prompt runtime in minutes (1-120)")
	cmd.Flags().StringVar(&repoFull, "repo", "", "GitHub repo in owner/repo form")
	cmd.Flags().IntVar(&repoSize, "repo-size-kb", 0, "Repo size in KB")
	_ = cmd.MarkFlagRequired("prompt")
	return cmd
}

// --- list (CANONICAL: /api/sessions/) ------------------------------------

func newSessionList(rt *Runtime) *cobra.Command {
	var (
		limit            int
		offset           int
		teamMode         bool
		includeDestroyed bool
		excludeSources   string
		excludeStates    string
	)
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List sessions (GET /api/sessions/)",
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}

			q := url.Values{}
			if limit > 0 {
				q.Set("limit", strconv.Itoa(limit))
			}
			if offset > 0 {
				q.Set("offset", strconv.Itoa(offset))
			}
			if teamMode {
				q.Set("team_mode", "true")
			}
			if includeDestroyed {
				q.Set("include_destroyed", "true")
			}
			if excludeSources != "" {
				q.Set("exclude_sources", excludeSources)
			}
			if excludeStates != "" {
				q.Set("exclude_states", excludeStates)
			}

			resp, err := c.Get("/sessions/", q)
			if err != nil {
				return err
			}
			rt.WriteJSON(resp)
			return nil
		},
	}
	cmd.Flags().IntVar(&limit, "limit", 0, "Number of results to return (1-100)")
	cmd.Flags().IntVar(&offset, "offset", 0, "Results to skip for pagination")
	cmd.Flags().BoolVar(&teamMode, "team-mode", false, "When org context is set, return team-visible sessions")
	cmd.Flags().BoolVar(&includeDestroyed, "include-destroyed", false, "Include destroyed/errored sessions")
	cmd.Flags().StringVar(&excludeSources, "exclude-sources", "", "Comma-separated sources to exclude (e.g. 'agents,api,slack')")
	cmd.Flags().StringVar(&excludeStates, "exclude-states", "", "Comma-separated states to exclude (e.g. 'destroyed,destroying')")
	return cmd
}

// --- get (CANONICAL: /api/sessions/{id}) ---------------------------------

func newSessionGet(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "get <session_id>",
		Short: "Get full session detail (GET /api/sessions/{id})",
		Long:  `Use 'runtm session status <id>' instead when you need the v0 polling shape with last_prompt fields for fire-and-forget workflows.`,
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			resp, err := c.Get("/sessions/"+url.PathEscape(args[0]), nil)
			if err != nil {
				return err
			}
			rt.WriteJSON(resp)
			return nil
		},
	}
}

// --- status (LEGACY v0: polling shape with last_prompt) ------------------

func newSessionStatus(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "status <session_id>",
		Short: "Poll-friendly status with last_prompt (GET /api/v0/sessions/{id})",
		Long: `Returns the v0 polling shape that includes the last_prompt field
(status, started_at, completed_at, cost_usd, summary). Use this after
'runtm session launch' to wait for completion.`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			resp, err := c.Get("/v0/sessions/"+url.PathEscape(args[0]), nil)
			if err != nil {
				return err
			}
			rt.WriteJSON(resp)
			return nil
		},
	}
}

// --- prompt streaming (LEGACY v0: synchronous SSE) -----------------------

func newSessionPrompt(rt *Runtime) *cobra.Command {
	var (
		model       string
		planMode    bool
		continueSes string
		newSession  bool
	)
	cmd := &cobra.Command{
		Use:   "prompt <session_id> <prompt>",
		Short: "Stream a prompt synchronously (POST /api/v0/sessions/{id}/prompt)",
		Long: `Sends a prompt and streams the agent's SSE response as JSON lines.

Each stdout line is a JSON object: {"event": "<type>", "data": <payload>}.
Terminates with an event named "done". For background runs, use
'runtm session launch' and poll 'runtm session status'.`,
		Args: cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}

			body := map[string]any{"prompt": args[1]}
			if model != "" {
				body["model"] = model
			}
			if planMode {
				body["plan_mode"] = true
			}
			if continueSes != "" {
				body["continue_session"] = continueSes
			}
			if newSession {
				body["new_session"] = true
			}

			path := "/v0/sessions/" + url.PathEscape(args[0]) + "/prompt"
			return c.StreamSSE(path, body, rt.Stdout)
		},
	}
	cmd.Flags().StringVar(&model, "model", "", "Model override (sonnet, opus, haiku, opusplan)")
	cmd.Flags().BoolVar(&planMode, "plan-mode", false, "Read-only plan mode")
	cmd.Flags().StringVar(&continueSes, "continue-session", "", "Claude Code session ID to resume")
	cmd.Flags().BoolVar(&newSession, "new-session", false, "Force a new agent session instead of resuming")
	return cmd
}

// --- destroy (CANONICAL: /api/sessions/{id}) -----------------------------

func newSessionDestroy(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "destroy <session_id>",
		Short: "Permanently destroy a session (DELETE /api/sessions/{id})",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			resp, err := c.Delete("/sessions/" + url.PathEscape(args[0]))
			if err != nil {
				return err
			}
			rt.WriteJSON(resp)
			return nil
		},
	}
}

// --- git (CANONICAL: /api/sessions/{id}/git, same body as v0) ------------

func newSessionGit(rt *Runtime) *cobra.Command {
	var (
		workingDir   string
		message      string
		branch       string
		branchSearch string
		prTitle      string
		prBody       string
		prBase       string
		branchSesID  string
	)
	cmd := &cobra.Command{
		Use:   "git <session_id> <operation>",
		Short: "Run a git operation inside the session (POST /api/sessions/{id}/git)",
		Long: `Operations: status, commit, push, pull, sync, create_branch, switch_branch,
list_branches, create_pr, create_branch_and_pr, init_repo.

Typical end-of-task flow:
  runtm session git <id> create_branch_and_pr --pr-title "Fix X" --pr-body "..."`,
		Args: cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}

			body := map[string]any{"operation": args[1]}
			if workingDir != "" {
				body["working_dir"] = workingDir
			}
			if message != "" {
				body["message"] = message
			}
			if branch != "" {
				body["branch"] = branch
			}
			if branchSearch != "" {
				body["branch_search"] = branchSearch
			}
			if prTitle != "" {
				body["pr_title"] = prTitle
			}
			if prBody != "" {
				body["pr_body"] = prBody
			}
			if prBase != "" {
				body["pr_base"] = prBase
			}
			if branchSesID != "" {
				body["branch_session_id"] = branchSesID
			}

			path := "/sessions/" + url.PathEscape(args[0]) + "/git"
			resp, err := c.PostJSON(path, body)
			if err != nil {
				return err
			}
			rt.WriteJSON(resp)
			return nil
		},
	}
	cmd.Flags().StringVar(&workingDir, "working-dir", "", "Directory containing the git repo (default /home/user)")
	cmd.Flags().StringVarP(&message, "message", "m", "", "Commit message (for commit)")
	cmd.Flags().StringVar(&branch, "branch", "", "Branch name (for create_branch/switch_branch)")
	cmd.Flags().StringVar(&branchSearch, "branch-search", "", "Filter for list_branches")
	cmd.Flags().StringVar(&prTitle, "pr-title", "", "Pull request title (for create_pr / create_branch_and_pr)")
	cmd.Flags().StringVar(&prBody, "pr-body", "", "Pull request body")
	cmd.Flags().StringVar(&prBase, "pr-base", "", "Base branch for the PR")
	cmd.Flags().StringVar(&branchSesID, "branch-session-id", "", "Session ID to derive branch name from")
	return cmd
}

// --- lifecycle: pause / resume / rename (CANONICAL) ----------------------

func newSessionPause(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "pause <session_id>",
		Short: "Pause a running sandbox (POST /api/sessions/{id}/pause)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			resp, err := c.PostJSON("/sessions/"+url.PathEscape(args[0])+"/pause", map[string]any{})
			if err != nil {
				return err
			}
			rt.WriteJSON(resp)
			return nil
		},
	}
}

func newSessionResume(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "resume <session_id>",
		Short: "Resume a paused sandbox (POST /api/sessions/{id}/resume)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			resp, err := c.PostJSON("/sessions/"+url.PathEscape(args[0])+"/resume", map[string]any{})
			if err != nil {
				return err
			}
			rt.WriteJSON(resp)
			return nil
		},
	}
}

func newSessionRename(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "rename <session_id> <name>",
		Short: "Rename a session (PATCH /api/sessions/{id}/name)",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			resp, err := c.PatchJSON("/sessions/"+url.PathEscape(args[0])+"/name", map[string]any{"name": args[1]})
			if err != nil {
				return err
			}
			rt.WriteJSON(resp)
			return nil
		},
	}
}

// --- file read / write / list (CANONICAL) --------------------------------

func newSessionFile(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "file",
		Short: "Read, write, and list files in a session sandbox",
	}
	cmd.AddCommand(newSessionFileRead(rt), newSessionFileWrite(rt), newSessionFileList(rt))
	return cmd
}

func newSessionFileRead(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "read <session_id> <path>",
		Short: "Read a file from the sandbox (GET /api/sessions/{id}/file)",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			q := url.Values{}
			q.Set("path", args[1])
			resp, err := c.Get("/sessions/"+url.PathEscape(args[0])+"/file", q)
			if err != nil {
				return err
			}
			rt.WriteJSON(resp)
			return nil
		},
	}
}

func newSessionFileWrite(rt *Runtime) *cobra.Command {
	var content string
	cmd := &cobra.Command{
		Use:   "write <session_id> <path>",
		Short: "Write a file to the sandbox (POST /api/sessions/{id}/file)",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			body := map[string]any{
				"path":    args[1],
				"content": content,
			}
			resp, err := c.PostJSON("/sessions/"+url.PathEscape(args[0])+"/file", body)
			if err != nil {
				return err
			}
			rt.WriteJSON(resp)
			return nil
		},
	}
	cmd.Flags().StringVarP(&content, "content", "c", "", "File content (required)")
	_ = cmd.MarkFlagRequired("content")
	return cmd
}

func newSessionFileList(rt *Runtime) *cobra.Command {
	var path string
	cmd := &cobra.Command{
		Use:   "list <session_id>",
		Short: "List files at a path in the sandbox (GET /api/sessions/{id}/files)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			q := url.Values{}
			if path != "" {
				q.Set("path", path)
			}
			resp, err := c.Get("/sessions/"+url.PathEscape(args[0])+"/files", q)
			if err != nil {
				return err
			}
			rt.WriteJSON(resp)
			return nil
		},
	}
	cmd.Flags().StringVar(&path, "path", "", "Directory inside the sandbox to list (default /home/user)")
	return cmd
}

// --- env get / set / delete (CANONICAL, uses secrets:* scopes) -----------

func newSessionEnv(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "env",
		Short: "Get, set, and delete session env vars (uses secrets:* scopes)",
	}
	cmd.AddCommand(newSessionEnvGet(rt), newSessionEnvSet(rt), newSessionEnvDelete(rt))
	return cmd
}

func newSessionEnvGet(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "get <session_id>",
		Short: "List session env vars (GET /api/sessions/{id}/env-vars)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			resp, err := c.Get("/sessions/"+url.PathEscape(args[0])+"/env-vars", nil)
			if err != nil {
				return err
			}
			rt.WriteJSON(resp)
			return nil
		},
	}
}

func newSessionEnvSet(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "set <session_id> KEY=VALUE [KEY=VALUE ...]",
		Short: "Set session env vars (PUT /api/sessions/{id}/env-vars)",
		Args:  cobra.MinimumNArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			pairs, err := parseKeyValuePairs(args[1:])
			if err != nil {
				return err
			}
			body := map[string]any{"env_vars": pairs}
			resp, err := c.PutJSON("/sessions/"+url.PathEscape(args[0])+"/env-vars", body)
			if err != nil {
				return err
			}
			rt.WriteJSON(resp)
			return nil
		},
	}
	return cmd
}

func newSessionEnvDelete(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "delete <session_id> <key>",
		Short: "Delete a session env var (DELETE /api/sessions/{id}/env-vars/{key})",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			path := "/sessions/" + url.PathEscape(args[0]) + "/env-vars/" + url.PathEscape(args[1])
			resp, err := c.Delete(path)
			if err != nil {
				return err
			}
			rt.WriteJSON(resp)
			return nil
		},
	}
}

// --- helpers --------------------------------------------------------------

func parseKeyValuePairs(args []string) (map[string]string, error) {
	out := make(map[string]string, len(args))
	for _, raw := range args {
		for i := 0; i < len(raw); i++ {
			if raw[i] == '=' {
				key := raw[:i]
				if key == "" {
					return nil, fmt.Errorf("invalid env var %q: empty key", raw)
				}
				out[key] = raw[i+1:]
				goto next
			}
		}
		return nil, fmt.Errorf("invalid env var %q: expected KEY=VALUE", raw)
	next:
	}
	return out, nil
}

func fallback(value, def string) string {
	if value != "" {
		return value
	}
	return def
}
