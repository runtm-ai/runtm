package cmd

import (
	"fmt"

	"github.com/spf13/cobra"
)

// Org-wide guardrail CONTENT: the allowlist rules, hooks, and network rules
// themselves. These are agent-directives (families allowlist / hook /
// network), distinct from `guardrails limits` and `guardrails allowlist`
// which manage the org's numeric limits and default allowlist policy.
//
// Scope a rule to templates or repos with attach/detach, exactly like skills.
//
// Routes: /api/agent-directives family CRUD, context:read / context:write.

func addGuardrailDirectiveCommands(cmd *cobra.Command, rt *Runtime) {
	cmd.AddCommand(
		newGuardrailFamilyCommand(rt, guardrailFamilySpec{
			use:      "rules",
			singular: "allowlist rule",
			family:   "allowlist",
			typeName: "allowlist_rule_v0",
			short:    "Command allowlist rules: allow, ask, or deny command patterns",
			long: `Allowlist rules decide which shell commands sessions may run: each rule is
{"kind": "allow|ask|deny", "pattern": "<glob>", "purpose": "..."}. Rules are
directives, so they attach to templates, repos, or the whole org.

  runtm-api guardrails rules create --name block-force-push \
    --kind deny --pattern 'git push --force*'
  runtm-api guardrails rules attach <id> --template <template_id>

The org's DEFAULT policy (allow-all vs allowlist mode) stays under
'runtm-api guardrails allowlist get|set'.`,
			contentFlags: func(c *cobra.Command, v *guardrailContentFlags) {
				c.Flags().StringVar(&v.kind, "kind", "", "Rule kind: allow, ask, or deny")
				c.Flags().StringVar(&v.pattern, "pattern", "", "Command pattern the rule matches, e.g. 'npm *'")
				c.Flags().StringVar(&v.purpose, "purpose", "", "Why the rule exists (shown to reviewers)")
			},
			buildContent: func(c *cobra.Command, v *guardrailContentFlags) (map[string]any, bool, error) {
				touched := c.Flags().Changed("kind") || c.Flags().Changed("pattern") || c.Flags().Changed("purpose")
				if !touched {
					return nil, false, nil
				}
				if v.kind == "" || v.pattern == "" {
					return nil, true, fmt.Errorf("--kind and --pattern are required (or pass --content)")
				}
				content := map[string]any{"kind": v.kind, "pattern": v.pattern}
				if v.purpose != "" {
					content["purpose"] = v.purpose
				}
				return content, true, nil
			},
		}),
		newGuardrailFamilyCommand(rt, guardrailFamilySpec{
			use:      "hooks",
			singular: "hook",
			family:   "hook",
			typeName: "hook_v0",
			short:    "Lifecycle hooks: run a script or prompt on agent events",
			long: `Hooks run a command or inject a prompt at agent lifecycle events
(PreToolUse, PostToolUse, UserPromptSubmit, Stop, SessionStart, ...). Hooks
are directives, so they attach to templates, repos, or the whole org.

  runtm-api guardrails hooks create --name lint-on-stop \
    --event Stop --script './scripts/lint.sh' --timeout 120
  runtm-api guardrails hooks create --name guard-prompts \
    --event UserPromptSubmit --hook-type prompt --prompt 'Never touch prod.'

Hook names must be lowercase slugs (a-z, 0-9, hyphens).`,
			contentFlags: func(c *cobra.Command, v *guardrailContentFlags) {
				c.Flags().StringVar(&v.event, "event", "", "Lifecycle event: PreToolUse, PostToolUse, UserPromptSubmit, Notification, Stop, SubagentStop, SessionStart, SessionEnd, PreCompact")
				c.Flags().StringVar(&v.hookType, "hook-type", "command", "Hook type: command (runs --script) or prompt (injects --prompt)")
				c.Flags().StringVar(&v.script, "script", "", "command hooks: the shell script to run")
				c.Flags().StringVar(&v.prompt, "prompt", "", "prompt hooks: the text to inject")
				c.Flags().StringVar(&v.matcher, "matcher", "", "Pre/PostToolUse: only fire for tools matching this pattern")
				c.Flags().IntVar(&v.timeout, "timeout", 0, "Seconds before the hook is killed (1-600, default 30)")
				c.Flags().BoolVar(&v.runAsync, "async", false, "command hooks on PostToolUse/Notification/Stop/SessionEnd: don't block the agent")
			},
			buildContent: func(c *cobra.Command, v *guardrailContentFlags) (map[string]any, bool, error) {
				touched := c.Flags().Changed("event") || c.Flags().Changed("script") ||
					c.Flags().Changed("prompt") || c.Flags().Changed("matcher") ||
					c.Flags().Changed("timeout") || c.Flags().Changed("async") ||
					c.Flags().Changed("hook-type")
				if !touched {
					return nil, false, nil
				}
				if v.event == "" {
					return nil, true, fmt.Errorf("--event is required (or pass --content)")
				}
				content := map[string]any{"event": v.event, "type": v.hookType}
				if v.script != "" {
					content["script"] = v.script
				}
				if v.prompt != "" {
					content["prompt"] = v.prompt
				}
				if v.matcher != "" {
					content["matcher"] = v.matcher
				}
				if c.Flags().Changed("timeout") {
					content["timeout"] = v.timeout
				}
				if v.runAsync {
					content["async"] = true
				}
				return content, true, nil
			},
		}),
		newGuardrailFamilyCommand(rt, guardrailFamilySpec{
			use:      "network",
			singular: "network rule",
			family:   "network",
			typeName: "network_rule_v0",
			short:    "Network egress rules: hosts and CIDRs sandboxes may reach",
			long: `Network rules declare the hosts or CIDR ranges sandbox egress may reach:
{"kind": "host|cidr", "value": "api.example.com", "purpose": "..."}. Rules
are directives, so they attach to templates, repos, or the whole org.

  runtm-api guardrails network create --name allow-stripe \
    --kind host --value api.stripe.com --purpose 'billing API'`,
			contentFlags: func(c *cobra.Command, v *guardrailContentFlags) {
				c.Flags().StringVar(&v.kind, "kind", "", "Rule kind: host or cidr")
				c.Flags().StringVar(&v.value, "value", "", "Hostname or CIDR range")
				c.Flags().StringVar(&v.purpose, "purpose", "", "Why the rule exists")
			},
			buildContent: func(c *cobra.Command, v *guardrailContentFlags) (map[string]any, bool, error) {
				touched := c.Flags().Changed("kind") || c.Flags().Changed("value") || c.Flags().Changed("purpose")
				if !touched {
					return nil, false, nil
				}
				if v.kind == "" || v.value == "" {
					return nil, true, fmt.Errorf("--kind and --value are required (or pass --content)")
				}
				content := map[string]any{"kind": v.kind, "value": v.value}
				if v.purpose != "" {
					content["purpose"] = v.purpose
				}
				return content, true, nil
			},
		}),
	)
}

// guardrailContentFlags is the union of per-family convenience flags.
type guardrailContentFlags struct {
	kind     string
	pattern  string
	purpose  string
	value    string
	event    string
	hookType string
	script   string
	prompt   string
	matcher  string
	timeout  int
	runAsync bool
}

type guardrailFamilySpec struct {
	use          string
	singular     string
	family       string // type_family for list/facets
	typeName     string // directive type for create
	short        string
	long         string
	contentFlags func(*cobra.Command, *guardrailContentFlags)
	buildContent func(*cobra.Command, *guardrailContentFlags) (map[string]any, bool, error)
}

// newGuardrailFamilyCommand assembles one family's command tree, reusing the
// generic directive subcommands for everything that is not content-shaped.
func newGuardrailFamilyCommand(rt *Runtime, spec guardrailFamilySpec) *cobra.Command {
	cmd := &cobra.Command{
		Use:   spec.use,
		Short: spec.short,
		Long:  spec.long,
	}
	cmd.AddCommand(
		newDirectiveList(rt, spec.singular, spec.family),
		newDirectiveGet(rt, spec.singular),
		newGuardrailDirectiveCreate(rt, spec),
		newGuardrailDirectiveUpdate(rt, spec),
		newDirectiveDelete(rt, spec.singular),
		newDirectiveAttachments(rt, spec.singular),
		newDirectiveAttach(rt, spec.singular),
		newDirectiveDetach(rt, spec.singular),
		newDirectiveLock(rt, spec.singular, true),
		newDirectiveLock(rt, spec.singular, false),
	)
	return cmd
}

func newGuardrailDirectiveCreate(rt *Runtime, spec guardrailFamilySpec) *cobra.Command {
	var (
		name        string
		displayName string
		description string
		contentJSON string
		labels      []string
		flags       guardrailContentFlags
	)
	cmd := &cobra.Command{
		Use:   "create",
		Short: fmt.Sprintf("Create an org %s", spec.singular),
		RunE: func(cmd *cobra.Command, args []string) error {
			if name == "" {
				return fmt.Errorf("--name is required")
			}
			var content map[string]any
			if contentJSON != "" {
				parsed, err := parseJSONObject(contentJSON)
				if err != nil {
					return fmt.Errorf("--content: %w", err)
				}
				content = parsed
			} else {
				built, _, err := spec.buildContent(cmd, &flags)
				if err != nil {
					return err
				}
				if built == nil {
					return fmt.Errorf("provide the %s content via flags or --content <json>", spec.singular)
				}
				content = built
			}
			body := map[string]any{
				"type":    spec.typeName,
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
			c, _, err := requireOrgClient(rt, spec.singular+"s")
			if err != nil {
				return err
			}
			resp, err := c.PostJSON(directivesListPath, body)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&name, "name", "", fmt.Sprintf("Unique %s name (required)", spec.singular))
	cmd.Flags().StringVar(&displayName, "display-name", "", "Human-readable name")
	cmd.Flags().StringVar(&description, "description", "", "Short description")
	cmd.Flags().StringVar(&contentJSON, "content", "", "Raw content object as JSON (overrides the typed flags)")
	cmd.Flags().StringArrayVar(&labels, "label", nil, "Label as KEY=VALUE (repeatable)")
	spec.contentFlags(cmd, &flags)
	_ = cmd.MarkFlagRequired("name")
	return cmd
}

func newGuardrailDirectiveUpdate(rt *Runtime, spec guardrailFamilySpec) *cobra.Command {
	var (
		displayName string
		description string
		contentJSON string
		labels      []string
		ownerTeam   string
		flags       guardrailContentFlags
	)
	cmd := &cobra.Command{
		Use:   "update <id>",
		Short: fmt.Sprintf("Update an org %s", spec.singular),
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			body := map[string]any{}
			if cmd.Flags().Changed("display-name") {
				body["display_name"] = displayName
			}
			if cmd.Flags().Changed("description") {
				body["description"] = description
			}
			if cmd.Flags().Changed("content") {
				parsed, err := parseJSONObject(contentJSON)
				if err != nil {
					return fmt.Errorf("--content: %w", err)
				}
				body["content"] = parsed
			} else {
				built, touched, err := spec.buildContent(cmd, &flags)
				if err != nil {
					return err
				}
				if touched {
					body["content"] = built
				}
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
				return fmt.Errorf("pass at least one field to update")
			}
			c, _, err := requireOrgClient(rt, spec.singular+"s")
			if err != nil {
				return err
			}
			resp, err := c.PatchJSON(directivePath(args[0]), body)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&displayName, "display-name", "", "New display name")
	cmd.Flags().StringVar(&description, "description", "", "New description")
	cmd.Flags().StringVar(&contentJSON, "content", "", "Replace the content object (JSON, overrides the typed flags)")
	cmd.Flags().StringArrayVar(&labels, "label", nil, "Replace labels; KEY=VALUE (repeatable)")
	cmd.Flags().StringVar(&ownerTeam, "owner-team", "", "Owning group (Better Auth team id); pass an empty string to make it org-wide")
	spec.contentFlags(cmd, &flags)
	return cmd
}
