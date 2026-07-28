package cmd

import (
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"strconv"
	"strings"

	"github.com/runtm-ai/runtm/packages/agent/internal/client"
	"github.com/spf13/cobra"
)

// This file implements the cloud CRUD commands for the three things a session
// can load: skills, MCP servers, and tools. They are exposed as separate
// top-level commands (`runtm-api skills|mcp|tools`) so users only ever deal
// with those concepts — never the underlying "directive" plumbing.
//
// Under the hood: skills and MCP servers are agent-directives (one endpoint
// family, distinguished by type); tools are knowledge integrations (a separate
// endpoint). That mapping is documented in the `runtm-integrations` skill, not
// surfaced in the command UX.

// NewMcpCommand returns `runtm-api mcp` — CRUD for MCP servers.
func NewMcpCommand(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "mcp",
		Short: "Create and manage MCP servers (stdio or http/sse)",
		Long: `Manage MCP servers that sessions can load. Two transports:
stdio (a local command) and http/sse (a remote URL).

Org-scoped: requires an org-scoped API key (--org cannot substitute for one).
Writes need the context:write scope on the key.`,
	}
	cmd.AddCommand(
		// "mcp" is the backend's type_family. Sending the type name instead
		// ("mcp_server") falls through to no type filter at all, which quietly
		// mixes skills into the results.
		newDirectiveList(rt, "MCP server", "mcp"),
		newDirectiveGet(rt, "MCP server"),
		newMcpCreate(rt),
		newMcpUpdate(rt),
		newDirectiveDelete(rt, "MCP server"),
		newDirectiveAttachments(rt, "MCP server"),
		newDirectiveAttach(rt, "MCP server"),
		newDirectiveDetach(rt, "MCP server"),
		newDirectiveResync(rt, "MCP server"),
		newDirectiveLock(rt, "MCP server", true),
		newDirectiveLock(rt, "MCP server", false),
		newDirectiveFacets(rt, "MCP server", "mcp"),
	)
	return cmd
}

// NewToolsCommand returns `runtm-api tools` — CRUD for tools (the dashboard's
// knowledge integrations: stored provider credentials).
func NewToolsCommand(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "tools",
		Short: "Create and manage tools (provider credentials)",
		Long: `Manage tools — stored provider credentials (e.g. bigquery, notion) that
sessions use. This command handles static-credential providers (service
accounts, API keys); OAuth providers are connected through the dashboard.

Org-scoped: requires an org-scoped API key (--org cannot substitute for one).
Writes need the integrations:write scope on the key.`,
	}
	cmd.AddCommand(
		newToolList(rt),
		newToolGet(rt),
		newToolCreate(rt),
		newToolUpdate(rt),
		newToolDelete(rt),
		newToolProvidersCommand(rt),
	)
	return cmd
}

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

// Cloud paths (relative to the /api/cloud base). List/create use the trailing
// slash to match the backend agent-directives route registered at "/".
const (
	directivesListPath = "/agent-directives/"
	knowledgeBasePath  = "/knowledge/integrations"
)

func directivePath(id string) string {
	return "/agent-directives/" + url.PathEscape(id)
}

func integrationPath(id string) string {
	return knowledgeBasePath + "/" + url.PathEscape(id)
}

// parseKeyVals turns ["A=1", "B=2"] into {"A":"1","B":"2"}. The value may
// contain "=" (only the first is the separator).
func parseKeyVals(pairs []string) (map[string]string, error) {
	out := map[string]string{}
	for _, p := range pairs {
		k, v, ok := strings.Cut(p, "=")
		if !ok || k == "" {
			return nil, fmt.Errorf("invalid KEY=VALUE pair: %q", p)
		}
		out[k] = v
	}
	return out, nil
}

// parseJSONObject parses a JSON object string into a map.
func parseJSONObject(raw string) (map[string]any, error) {
	var m map[string]any
	if err := json.Unmarshal([]byte(raw), &m); err != nil {
		return nil, fmt.Errorf("invalid JSON object: %w", err)
	}
	return m, nil
}

// skillContentFromMarkdown builds a skill content payload from a single
// markdown file on disk (the common case — one SKILL.md).
func skillContentFromMarkdown(path, entry string) (map[string]any, error) {
	data, err := os.ReadFile(path) // #nosec G304 -- path is the user's --md CLI flag; reading the named file is the command's purpose
	if err != nil {
		return nil, fmt.Errorf("read %s: %w", path, err)
	}
	return map[string]any{
		"entry_md": entry,
		"files": []map[string]any{
			{"path": entry, "mode": "text", "inline": string(data)},
		},
	}, nil
}

// listQuery builds the shared pagination query for list commands.
func listQuery(pageSize int, pageToken string) url.Values {
	q := url.Values{}
	if pageSize > 0 {
		q.Set("page_size", strconv.Itoa(pageSize))
	}
	if pageToken != "" {
		q.Set("page_token", pageToken)
	}
	return q
}

// ---------------------------------------------------------------------------
// skill create/update (cloud) — wired into `runtm-api skills` in skills_cmd.go
// ---------------------------------------------------------------------------

func newSkillCreate(rt *Runtime) *cobra.Command {
	var (
		name        string
		displayName string
		description string
		mdPath      string
		entry       string
		contentJSON string
		labels      []string
	)
	cmd := &cobra.Command{
		Use:   "create",
		Short: "Create a skill",
		Long: `Create a skill. Provide the body with either:
  --md <path>        a markdown file used as the skill's entry (default SKILL.md)
  --content <json>   a raw skill content object (full control over files/requires)

Example:
  runtm-api skills create --name deploy-checks \
    --display-name "Pre-deploy checks" --md ./SKILL.md`,
		RunE: func(cmd *cobra.Command, args []string) error {
			if name == "" {
				return fmt.Errorf("--name is required")
			}
			content, err := resolveSkillContent(mdPath, entry, contentJSON)
			if err != nil {
				return err
			}
			c, _, err := requireOrgClient(rt, "skills")
			if err != nil {
				return err
			}
			body := map[string]any{
				"type":    "skill_v0",
				"name":    name,
				"content": content,
			}
			if displayName != "" {
				body["display_name"] = displayName
			}
			if description != "" {
				body["description"] = description
			}
			if len(labels) > 0 {
				lbl, perr := parseKeyVals(labels)
				if perr != nil {
					return perr
				}
				body["labels"] = lbl
			}
			resp, err := c.PostJSON(directivesListPath, body)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&name, "name", "", "Unique skill name/slug (required)")
	cmd.Flags().StringVar(&displayName, "display-name", "", "Human-readable name")
	cmd.Flags().StringVar(&description, "description", "", "Short description")
	cmd.Flags().StringVar(&mdPath, "md", "", "Path to a markdown file used as the skill entry")
	cmd.Flags().StringVar(&entry, "entry", "SKILL.md", "Entry filename for --md content")
	cmd.Flags().StringVar(&contentJSON, "content", "", "Raw skill content object as JSON (overrides --md)")
	cmd.Flags().StringArrayVar(&labels, "label", nil, "Label as KEY=VALUE (repeatable)")
	_ = cmd.MarkFlagRequired("name")
	return cmd
}

func newSkillUpdate(rt *Runtime) *cobra.Command {
	var (
		displayName string
		description string
		mdPath      string
		entry       string
		contentJSON string
		labels      []string
		ownerTeam   string
	)
	cmd := &cobra.Command{
		Use:   "update <id>",
		Short: "Update a skill",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			body := map[string]any{}
			if cmd.Flags().Changed("display-name") {
				body["display_name"] = displayName
			}
			if cmd.Flags().Changed("description") {
				body["description"] = description
			}
			if cmd.Flags().Changed("md") || cmd.Flags().Changed("content") {
				content, err := resolveSkillContent(mdPath, entry, contentJSON)
				if err != nil {
					return err
				}
				body["content"] = content
			}
			if cmd.Flags().Changed("label") {
				lbl, perr := parseKeyVals(labels)
				if perr != nil {
					return perr
				}
				body["labels"] = lbl
			}
			if cmd.Flags().Changed("owner-team") {
				body["owner_team_id"] = ownerTeamValue(ownerTeam)
			}
			if len(body) == 0 {
				return fmt.Errorf("pass at least one field to update (--display-name, --description, --md/--content, --label, --owner-team)")
			}
			c, _, err := requireOrgClient(rt, "skills")
			if err != nil {
				return err
			}
			resp, err := c.PatchJSON(directivePath(args[0]), body)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&displayName, "display-name", "", "New display name")
	cmd.Flags().StringVar(&description, "description", "", "New description")
	cmd.Flags().StringVar(&mdPath, "md", "", "Replace content from a markdown file")
	cmd.Flags().StringVar(&entry, "entry", "SKILL.md", "Entry filename for --md content")
	cmd.Flags().StringVar(&contentJSON, "content", "", "Replace content with a raw JSON object")
	cmd.Flags().StringArrayVar(&labels, "label", nil, "Replace labels; KEY=VALUE (repeatable)")
	cmd.Flags().StringVar(&ownerTeam, "owner-team", "", "Owning group (Better Auth team id); pass an empty string to make it org-wide")
	return cmd
}

// ownerTeamValue maps the --owner-team flag to the API's tri-state field:
// a team id sets the owning group, an empty string sends explicit null
// (org-wide). Absence of the flag means unchanged, handled by the caller.
func ownerTeamValue(v string) any {
	if v == "" {
		return nil
	}
	return v
}

// resolveSkillContent builds the content object from --content (raw JSON, wins)
// or --md (markdown file). Errors when neither is usable.
func resolveSkillContent(mdPath, entry, contentJSON string) (map[string]any, error) {
	if contentJSON != "" {
		return parseJSONObject(contentJSON)
	}
	if mdPath != "" {
		if entry == "" {
			entry = "SKILL.md"
		}
		return skillContentFromMarkdown(mdPath, entry)
	}
	return nil, fmt.Errorf("provide skill content via --md <path> or --content <json>")
}

// ---------------------------------------------------------------------------
// mcp create/update
// ---------------------------------------------------------------------------

func newMcpCreate(rt *Runtime) *cobra.Command {
	var (
		name        string
		displayName string
		description string
		transport   string
		command     string
		mcpArgs     []string
		envs        []string
		serverURL   string
		headers     []string
		contentJSON string
		labels      []string
	)
	cmd := &cobra.Command{
		Use:   "create",
		Short: "Create an MCP server",
		Long: `Create an MCP server.

stdio (default):
  runtm-api mcp create --name files --transport stdio \
    --command npx --arg -y --arg @scope/server --env TOKEN=abc

http/sse:
  runtm-api mcp create --name remote --transport http \
    --url https://mcp.example.com --header "Authorization=Bearer xyz"

Or pass the full content object with --content <json>.`,
		RunE: func(cmd *cobra.Command, args []string) error {
			if name == "" {
				return fmt.Errorf("--name is required")
			}
			content, err := resolveMcpContent(transport, command, mcpArgs, envs, serverURL, headers, contentJSON)
			if err != nil {
				return err
			}
			c, _, err := requireOrgClient(rt, "MCP servers")
			if err != nil {
				return err
			}
			body := map[string]any{
				"type":    "mcp_server_v0",
				"name":    name,
				"content": content,
			}
			if displayName != "" {
				body["display_name"] = displayName
			}
			if description != "" {
				body["description"] = description
			}
			if len(labels) > 0 {
				lbl, perr := parseKeyVals(labels)
				if perr != nil {
					return perr
				}
				body["labels"] = lbl
			}
			resp, err := c.PostJSON(directivesListPath, body)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&name, "name", "", "Unique MCP server name (required)")
	cmd.Flags().StringVar(&displayName, "display-name", "", "Human-readable name")
	cmd.Flags().StringVar(&description, "description", "", "Short description")
	cmd.Flags().StringVar(&transport, "transport", "stdio", "Transport: stdio, http, or sse")
	cmd.Flags().StringVar(&command, "command", "", "stdio: executable to launch")
	cmd.Flags().StringArrayVar(&mcpArgs, "arg", nil, "stdio: command argument (repeatable, ordered)")
	cmd.Flags().StringArrayVar(&envs, "env", nil, "stdio: environment var KEY=VALUE (repeatable)")
	cmd.Flags().StringVar(&serverURL, "url", "", "http/sse: server URL")
	cmd.Flags().StringArrayVar(&headers, "header", nil, "http/sse: header KEY=VALUE (repeatable)")
	cmd.Flags().StringVar(&contentJSON, "content", "", "Raw MCP content object as JSON (overrides the flags above)")
	cmd.Flags().StringArrayVar(&labels, "label", nil, "Label as KEY=VALUE (repeatable)")
	_ = cmd.MarkFlagRequired("name")
	return cmd
}

func newMcpUpdate(rt *Runtime) *cobra.Command {
	var (
		displayName string
		description string
		transport   string
		command     string
		mcpArgs     []string
		envs        []string
		serverURL   string
		headers     []string
		contentJSON string
		labels      []string
		ownerTeam   string
	)
	cmd := &cobra.Command{
		Use:   "update <id>",
		Short: "Update an MCP server",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			body := map[string]any{}
			if cmd.Flags().Changed("display-name") {
				body["display_name"] = displayName
			}
			if cmd.Flags().Changed("description") {
				body["description"] = description
			}
			contentTouched := cmd.Flags().Changed("content") ||
				cmd.Flags().Changed("transport") || cmd.Flags().Changed("command") ||
				cmd.Flags().Changed("arg") || cmd.Flags().Changed("env") ||
				cmd.Flags().Changed("url") || cmd.Flags().Changed("header")
			if contentTouched {
				content, err := resolveMcpContent(transport, command, mcpArgs, envs, serverURL, headers, contentJSON)
				if err != nil {
					return err
				}
				body["content"] = content
			}
			if cmd.Flags().Changed("label") {
				lbl, perr := parseKeyVals(labels)
				if perr != nil {
					return perr
				}
				body["labels"] = lbl
			}
			if cmd.Flags().Changed("owner-team") {
				body["owner_team_id"] = ownerTeamValue(ownerTeam)
			}
			if len(body) == 0 {
				return fmt.Errorf("pass at least one field to update (--display-name, --description, MCP content flags, --label, --owner-team)")
			}
			c, _, err := requireOrgClient(rt, "MCP servers")
			if err != nil {
				return err
			}
			resp, err := c.PatchJSON(directivePath(args[0]), body)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&displayName, "display-name", "", "New display name")
	cmd.Flags().StringVar(&description, "description", "", "New description")
	cmd.Flags().StringVar(&transport, "transport", "stdio", "Transport: stdio, http, or sse")
	cmd.Flags().StringVar(&command, "command", "", "stdio: executable to launch")
	cmd.Flags().StringArrayVar(&mcpArgs, "arg", nil, "stdio: command argument (repeatable, ordered)")
	cmd.Flags().StringArrayVar(&envs, "env", nil, "stdio: environment var KEY=VALUE (repeatable)")
	cmd.Flags().StringVar(&serverURL, "url", "", "http/sse: server URL")
	cmd.Flags().StringArrayVar(&headers, "header", nil, "http/sse: header KEY=VALUE (repeatable)")
	cmd.Flags().StringVar(&contentJSON, "content", "", "Raw MCP content object as JSON")
	cmd.Flags().StringArrayVar(&labels, "label", nil, "Replace labels; KEY=VALUE (repeatable)")
	cmd.Flags().StringVar(&ownerTeam, "owner-team", "", "Owning group (Better Auth team id); pass an empty string to make it org-wide")
	return cmd
}

// resolveMcpContent builds an MCP content object from --content (raw JSON, wins)
// or the structured transport flags.
func resolveMcpContent(transport, command string, mcpArgs, envs []string, serverURL string, headers []string, contentJSON string) (map[string]any, error) {
	if contentJSON != "" {
		return parseJSONObject(contentJSON)
	}
	if transport == "" {
		transport = "stdio"
	}
	content := map[string]any{"transport": transport}
	switch transport {
	case "stdio":
		if command == "" {
			return nil, fmt.Errorf("stdio MCP servers require --command")
		}
		content["command"] = command
		if len(mcpArgs) > 0 {
			content["args"] = mcpArgs
		}
		if len(envs) > 0 {
			env, err := parseKeyVals(envs)
			if err != nil {
				return nil, err
			}
			content["env"] = env
		}
	case "http", "sse":
		if serverURL == "" {
			return nil, fmt.Errorf("%s MCP servers require --url", transport)
		}
		content["url"] = serverURL
		if len(headers) > 0 {
			hdr, err := parseKeyVals(headers)
			if err != nil {
				return nil, err
			}
			content["headers"] = hdr
		}
	default:
		return nil, fmt.Errorf("invalid --transport %q (expected stdio, http, or sse)", transport)
	}
	return content, nil
}

// ---------------------------------------------------------------------------
// tool (knowledge integration) commands
// ---------------------------------------------------------------------------

func newToolList(rt *Runtime) *cobra.Command {
	var (
		scope     string
		provider  string
		pageSize  int
		pageToken string
	)
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List tools",
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, "tools")
			if err != nil {
				return err
			}
			q := listQuery(pageSize, pageToken)
			if scope != "" {
				q.Set("scope", scope)
			}
			if provider != "" {
				q.Set("provider", provider)
			}
			resp, err := c.Get(knowledgeBasePath, q)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&scope, "scope", "", "Filter by scope: org or personal")
	cmd.Flags().StringVar(&provider, "provider", "", "Filter by provider slug")
	cmd.Flags().IntVar(&pageSize, "page-size", 0, "Results per page (1-100)")
	cmd.Flags().StringVar(&pageToken, "page-token", "", "Pagination cursor")
	return cmd
}

func newToolGet(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "get <id>",
		Short: "Get a tool",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, "tools")
			if err != nil {
				return err
			}
			resp, err := c.Get(integrationPath(args[0]), nil)
			return runJSON(rt, resp, err)
		},
	}
}

func newToolCreate(rt *Runtime) *cobra.Command {
	var (
		provider        string
		authMethod      string
		scope           string
		displayName     string
		credentialsJSON string
		metadataJSON    string
		defaultMode     string
	)
	cmd := &cobra.Command{
		Use:   "create",
		Short: "Create a tool (store provider credentials)",
		Long: `Store static credentials for a provider so sessions can use the tool.

Example:
  runtm-api tools create --provider bigquery \
    --auth-method service_account --scope org \
    --credentials '{"service_account_json":"{...}"}' \
    --provider-metadata '{"project_id":"my-gcp-project"}'`,
		RunE: func(cmd *cobra.Command, args []string) error {
			if provider == "" || authMethod == "" {
				return fmt.Errorf("--provider and --auth-method are required")
			}
			if credentialsJSON == "" {
				return fmt.Errorf("--credentials <json> is required")
			}
			creds, err := parseJSONObject(credentialsJSON)
			if err != nil {
				return fmt.Errorf("--credentials: %w", err)
			}
			c, _, err := requireOrgClient(rt, "tools")
			if err != nil {
				return err
			}
			body := map[string]any{
				"provider":    provider,
				"auth_method": authMethod,
				"scope":       scope,
				"credentials": creds,
			}
			if displayName != "" {
				body["display_name"] = displayName
			}
			if metadataJSON != "" {
				meta, merr := parseJSONObject(metadataJSON)
				if merr != nil {
					return fmt.Errorf("--provider-metadata: %w", merr)
				}
				body["provider_metadata"] = meta
			}
			if defaultMode != "" {
				body["default_mode"] = defaultMode
			}
			resp, err := c.PostJSON(knowledgeBasePath, body)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&provider, "provider", "", "Provider slug, e.g. bigquery, notion (required)")
	cmd.Flags().StringVar(&authMethod, "auth-method", "", "Auth method, e.g. service_account, api_key (required)")
	cmd.Flags().StringVar(&scope, "scope", "org", "Scope: org or personal")
	cmd.Flags().StringVar(&displayName, "display-name", "", "Human-readable name")
	cmd.Flags().StringVar(&credentialsJSON, "credentials", "", "Credentials object as JSON (required)")
	cmd.Flags().StringVar(&metadataJSON, "provider-metadata", "", "Provider metadata object as JSON")
	cmd.Flags().StringVar(&defaultMode, "default-mode", "", "Default permission mode (e.g. ask, allow)")
	_ = cmd.MarkFlagRequired("provider")
	_ = cmd.MarkFlagRequired("auth-method")
	_ = cmd.MarkFlagRequired("credentials")
	return cmd
}

func newToolUpdate(rt *Runtime) *cobra.Command {
	var (
		displayName   string
		metadataJSON  string
		defaultMode   string
		toolPermsJSON string
	)
	cmd := &cobra.Command{
		Use:   "update <id>",
		Short: "Update a tool",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			body := map[string]any{}
			if cmd.Flags().Changed("display-name") {
				body["display_name"] = displayName
			}
			if cmd.Flags().Changed("provider-metadata") {
				meta, merr := parseJSONObject(metadataJSON)
				if merr != nil {
					return fmt.Errorf("--provider-metadata: %w", merr)
				}
				body["provider_metadata"] = meta
			}
			if cmd.Flags().Changed("default-mode") {
				body["default_mode"] = defaultMode
			}
			if cmd.Flags().Changed("tool-permissions") {
				tp, terr := parseJSONObject(toolPermsJSON)
				if terr != nil {
					return fmt.Errorf("--tool-permissions: %w", terr)
				}
				body["tool_permissions"] = tp
			}
			if len(body) == 0 {
				return fmt.Errorf("pass at least one field to update (--display-name, --provider-metadata, --default-mode, --tool-permissions)")
			}
			c, _, err := requireOrgClient(rt, "tools")
			if err != nil {
				return err
			}
			resp, err := c.PatchJSON(integrationPath(args[0]), body)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&displayName, "display-name", "", "New display name")
	cmd.Flags().StringVar(&metadataJSON, "provider-metadata", "", "Replace provider metadata (JSON object)")
	cmd.Flags().StringVar(&defaultMode, "default-mode", "", "Default permission mode")
	cmd.Flags().StringVar(&toolPermsJSON, "tool-permissions", "", "Per-tool permission overrides (JSON object)")
	return cmd
}

func newToolDelete(rt *Runtime) *cobra.Command {
	var yes bool
	cmd := &cobra.Command{
		Use:   "delete <id>",
		Short: "Delete a tool",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if !yes {
				rt.WriteObject(map[string]any{
					"error": "Destructive operation requires --yes to confirm.",
					"hint":  "Pass --yes when you are sure.",
				})
				return errSilent
			}
			c, _, err := requireOrgClient(rt, "tools")
			if err != nil {
				return err
			}
			resp, err := c.Delete(integrationPath(args[0]))
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().BoolVar(&yes, "yes", false, "Confirm deletion")
	return cmd
}

// ---------------------------------------------------------------------------
// Shared agent-directive subcommands (skill + mcp share one endpoint family)
// ---------------------------------------------------------------------------

// newDirectiveList builds a `list` subcommand for a skill/MCP type.
// singular is the user-facing noun ("skill", "MCP server"); typeFamily is the
// backend query value ("skill", "mcp_server").
func newDirectiveList(rt *Runtime, singular, typeFamily string) *cobra.Command {
	var (
		templateID     string
		repo           string
		pageSize       int
		pageToken      string
		includeContent bool
	)
	cmd := &cobra.Command{
		Use:   "list",
		Short: fmt.Sprintf("List %ss (--template to scope to one template)", singular),
		Long: fmt.Sprintf(`List the %ss in your org.

  --template <id>   only the %ss a session from that template loads.
                    Equivalent to 'runtm-api template mcp <id>'.
  --repo owner/name only the %ss attached to that repo.

Both scoped forms include org-wide items (those attached with --all).`,
			singular, singular, singular),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, singular+"s")
			if err != nil {
				return err
			}
			q := listQuery(pageSize, pageToken)
			q.Set("type_family", typeFamily)
			if templateID != "" {
				q.Set("template_id", templateID)
			}
			if repo != "" {
				q.Set("repo_full_name", repo)
			}
			if includeContent {
				q.Set("include_content", "true")
			}
			resp, err := c.Get(directivesListPath, q)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&templateID, "template", "", fmt.Sprintf("Only %ss a session from this template loads", singular))
	cmd.Flags().StringVar(&repo, "repo", "", fmt.Sprintf("Only %ss attached to this repo (owner/name)", singular))
	cmd.Flags().IntVar(&pageSize, "page-size", 0, "Results per page (1-100)")
	cmd.Flags().StringVar(&pageToken, "page-token", "", "Pagination cursor")
	cmd.Flags().BoolVar(&includeContent, "include-content", false, "Include each item's content payload")
	return cmd
}

func newDirectiveGet(rt *Runtime, singular string) *cobra.Command {
	var includeContent bool
	cmd := &cobra.Command{
		Use:   "get <id>",
		Short: fmt.Sprintf("Get a %s", singular),
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, singular+"s")
			if err != nil {
				return err
			}
			q := url.Values{}
			if includeContent {
				q.Set("include_content", "true")
			}
			resp, err := c.Get(directivePath(args[0]), q)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().BoolVar(&includeContent, "include-content", true, "Include the content payload")
	return cmd
}

func newDirectiveDelete(rt *Runtime, singular string) *cobra.Command {
	var yes bool
	cmd := &cobra.Command{
		Use:   "delete <id>",
		Short: fmt.Sprintf("Delete a %s", singular),
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if !yes {
				rt.WriteObject(map[string]any{
					"error": "Destructive operation requires --yes to confirm.",
					"hint":  "Pass --yes when you are sure.",
				})
				return errSilent
			}
			c, _, err := requireOrgClient(rt, singular+"s")
			if err != nil {
				return err
			}
			resp, err := c.Delete(directivePath(args[0]))
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().BoolVar(&yes, "yes", false, "Confirm deletion")
	return cmd
}

// ---------------------------------------------------------------------------
// Attachments — scope a skill/MCP to templates, repos, or all repos
//
// A directive (skill or MCP server) is loaded into a session only where it is
// attached. Attaching to a template makes every session launched from that
// template load it. Backed by:
//
//	GET /api/agent-directives/{id}/attachments   (list current scope)
//	PUT /api/agent-directives/{id}/attachments   (replace the full scope)
//
// The PUT is a full replace, so `attach`/`detach` read the current scope and
// merge by default — only `--replace` / `--clear` set it wholesale. The three
// scopes (templates, repos, all-repos) are mutually exclusive with all-repos:
// the backend rejects combining applies_to_all with explicit lists.
// ---------------------------------------------------------------------------

func attachmentsPath(id string) string {
	return directivePath(id) + "/attachments"
}

// attachmentRow mirrors the fields of DirectiveAttachmentResource we read back.
type attachmentRow struct {
	RepoFullName *string `json:"repo_full_name"`
	AppliesToAll bool    `json:"applies_to_all"`
	TemplateID   *string `json:"template_id"`
}

type attachmentsResp struct {
	Attachments []attachmentRow `json:"attachments"`
}

func (r *attachmentsResp) templateIDs() []string {
	out := []string{}
	for _, a := range r.Attachments {
		if a.TemplateID != nil && *a.TemplateID != "" {
			out = append(out, *a.TemplateID)
		}
	}
	return out
}

func (r *attachmentsResp) repoNames() []string {
	out := []string{}
	for _, a := range r.Attachments {
		if a.RepoFullName != nil && *a.RepoFullName != "" {
			out = append(out, *a.RepoFullName)
		}
	}
	return out
}

func (r *attachmentsResp) appliesToAll() bool {
	for _, a := range r.Attachments {
		if a.AppliesToAll {
			return true
		}
	}
	return false
}

// fetchAttachments reads a directive's current attachment scope.
func fetchAttachments(c *client.Client, id string) (*attachmentsResp, error) {
	raw, err := c.Get(attachmentsPath(id), nil)
	if err != nil {
		return nil, err
	}
	var out attachmentsResp
	if err := json.Unmarshal(raw, &out); err != nil {
		return nil, fmt.Errorf("parse current attachments: %w", err)
	}
	return &out, nil
}

// mergeUnique returns base ∪ add, order-preserving and de-duplicated.
func mergeUnique(base, add []string) []string {
	seen := map[string]bool{}
	out := []string{}
	for _, s := range append(append([]string{}, base...), add...) {
		if s == "" || seen[s] {
			continue
		}
		seen[s] = true
		out = append(out, s)
	}
	return out
}

// without returns base with every element of remove dropped.
func without(base, remove []string) []string {
	rm := map[string]bool{}
	for _, s := range remove {
		rm[s] = true
	}
	out := []string{}
	for _, s := range base {
		if !rm[s] {
			out = append(out, s)
		}
	}
	return out
}

// nonNil guarantees a JSON array (not null) for list body fields, which the
// backend's list[str] fields require.
func nonNil(s []string) []string {
	if s == nil {
		return []string{}
	}
	return s
}

func newDirectiveAttachments(rt *Runtime, singular string) *cobra.Command {
	return &cobra.Command{
		Use:   "attachments <id>",
		Short: fmt.Sprintf("List where a %s is attached (templates, repos, or all repos)", singular),
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, singular+"s")
			if err != nil {
				return err
			}
			resp, err := c.Get(attachmentsPath(args[0]), nil)
			return runJSON(rt, resp, err)
		},
	}
}

func newDirectiveAttach(rt *Runtime, singular string) *cobra.Command {
	var (
		templates []string
		repos     []string
		all       bool
		replace   bool
	)
	cmd := &cobra.Command{
		Use:   "attach <id>",
		Short: fmt.Sprintf("Attach a %s to templates, repos, or all repos", singular),
		Long: fmt.Sprintf(`Attach a %s so sessions load it. Scope it to one or more templates
(--template, repeatable), repos (--repo owner/name, repeatable), or every repo
in the org (--all).

Merges with the existing scope by default, so repeated calls add attachments.
Pass --replace to set the exact scope instead. --all is mutually exclusive with
--template/--repo and supersedes any existing scoped attachments.

Examples:
  runtm-api %ss attach <id> --template <template_id>
  runtm-api %ss attach <id> --template <t1> --template <t2>
  runtm-api %ss attach <id> --repo acme/api --replace
  runtm-api %ss attach <id> --all`, singular, singular, singular, singular, singular),
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if all && (len(templates) > 0 || len(repos) > 0) {
				return fmt.Errorf("--all cannot be combined with --template or --repo")
			}
			if !all && len(templates) == 0 && len(repos) == 0 {
				return fmt.Errorf("pass at least one of --template, --repo, or --all")
			}
			c, _, err := requireOrgClient(rt, singular+"s")
			if err != nil {
				return err
			}

			var body map[string]any
			switch {
			case all:
				body = map[string]any{"applies_to_all": true}
			case replace:
				body = map[string]any{
					"template_ids":    nonNil(templates),
					"repo_full_names": nonNil(repos),
				}
			default:
				// Merge with the current scope. Attaching to a template/repo
				// switches off all-repos scope (the two can't coexist).
				existing, gerr := fetchAttachments(c, args[0])
				if gerr != nil {
					return gerr
				}
				body = map[string]any{
					"template_ids":    mergeUnique(existing.templateIDs(), templates),
					"repo_full_names": mergeUnique(existing.repoNames(), repos),
				}
			}
			resp, err := c.PutJSON(attachmentsPath(args[0]), body)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringArrayVar(&templates, "template", nil, "Template ID to attach to (repeatable)")
	cmd.Flags().StringArrayVar(&repos, "repo", nil, "Repo owner/name to attach to (repeatable)")
	cmd.Flags().BoolVar(&all, "all", false, "Attach to every repo in the org (mutually exclusive with --template/--repo)")
	cmd.Flags().BoolVar(&replace, "replace", false, "Replace the full attachment scope instead of merging")
	return cmd
}

func newDirectiveDetach(rt *Runtime, singular string) *cobra.Command {
	var (
		templates []string
		repos     []string
		all       bool
		clearAll  bool
	)
	cmd := &cobra.Command{
		Use:   "detach <id>",
		Short: fmt.Sprintf("Detach a %s from templates, repos, or all repos", singular),
		Long: fmt.Sprintf(`Remove a %s's attachments while leaving the rest in place.

Pass --template/--repo to drop specific scopes, --all to remove the all-repos
attachment, or --clear to remove every attachment at once.

Examples:
  runtm-api %ss detach <id> --template <template_id>
  runtm-api %ss detach <id> --all
  runtm-api %ss detach <id> --clear`, singular, singular, singular, singular),
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, singular+"s")
			if err != nil {
				return err
			}

			if clearAll {
				resp, err := c.PutJSON(attachmentsPath(args[0]), map[string]any{
					"template_ids":    []string{},
					"repo_full_names": []string{},
				})
				return runJSON(rt, resp, err)
			}
			if !all && len(templates) == 0 && len(repos) == 0 {
				return fmt.Errorf("pass --template/--repo to remove, --all to remove the all-repos attachment, or --clear to remove everything")
			}

			existing, gerr := fetchAttachments(c, args[0])
			if gerr != nil {
				return gerr
			}
			remTemplates := without(existing.templateIDs(), templates)
			remRepos := without(existing.repoNames(), repos)

			// Keep the all-repos scope unless this call targets it. (It never
			// coexists with template/repo scopes, so there's nothing to merge.)
			var body map[string]any
			if existing.appliesToAll() && !all && len(remTemplates) == 0 && len(remRepos) == 0 {
				body = map[string]any{"applies_to_all": true}
			} else {
				body = map[string]any{
					"template_ids":    remTemplates,
					"repo_full_names": remRepos,
				}
			}
			resp, err := c.PutJSON(attachmentsPath(args[0]), body)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringArrayVar(&templates, "template", nil, "Template ID to detach from (repeatable)")
	cmd.Flags().StringArrayVar(&repos, "repo", nil, "Repo owner/name to detach from (repeatable)")
	cmd.Flags().BoolVar(&all, "all", false, "Remove the all-repos attachment")
	cmd.Flags().BoolVar(&clearAll, "clear", false, "Remove every attachment")
	return cmd
}
