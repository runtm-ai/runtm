package cmd

import (
	"fmt"
	"net/url"
	"strconv"

	"github.com/spf13/cobra"
)

// Template context + template-scoped guardrails.
//
// Routes (all under /api/org-templates/{template_id}):
//
//	GET  /context                    context:read
//	PUT  /context                    context:write
//	GET  /context:resolve            context:read
//	GET  /guardrails                 guardrails:read
//	POST /guardrails                 guardrails:write
//	PATCH  /guardrails/{id}          guardrails:write
//	DELETE /guardrails/{id}          guardrails:write
//	GET  /guardrails:resolve         guardrails:read

func templateSubPath(templateID, suffix string) string {
	return "/org-templates/" + url.PathEscape(templateID) + suffix
}

// --- context ----------------------------------------------------------------

func newTemplateContext(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "context",
		Short: "Get, set, and resolve a template's custom instructions",
		Long: `Template context is the instruction block injected into every session
launched from the template. 'resolve' answers the debugging question: what
will a session actually receive, org block plus template block, in order.`,
	}
	cmd.AddCommand(
		newTemplateContextGet(rt),
		newTemplateContextSet(rt),
		newTemplateContextResolve(rt),
	)
	return cmd
}

func newTemplateContextGet(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "get <template_id>",
		Short: "Read the template's context block (GET /api/org-templates/{id}/context)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, "org templates")
			if err != nil {
				return err
			}
			resp, err := c.Get(templateSubPath(args[0], "/context"), nil)
			return runJSON(rt, resp, err)
		},
	}
}

func newTemplateContextSet(rt *Runtime) *cobra.Command {
	var (
		text  string
		clear bool
	)
	cmd := &cobra.Command{
		Use:   "set <template_id>",
		Short: "Replace or clear the template's context block (PUT /api/org-templates/{id}/context)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if clear == (text != "") {
				return fmt.Errorf("pass exactly one of --text or --clear")
			}
			body := map[string]any{}
			if clear {
				body["content"] = nil
			} else {
				body["content"] = text
			}
			c, _, err := requireOrgClient(rt, "org templates")
			if err != nil {
				return err
			}
			resp, err := c.PutJSON(templateSubPath(args[0], "/context"), body)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVarP(&text, "text", "t", "", "New context content (max 20000 chars)")
	cmd.Flags().BoolVar(&clear, "clear", false, "Clear the template context")
	return cmd
}

func newTemplateContextResolve(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "resolve <template_id>",
		Short: "What sessions actually receive (GET /api/org-templates/{id}/context:resolve)",
		Long: `Returns the effective instruction stack for the template: the org block
and the template block in application order, plus the combined
effective_context string.`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, "org templates")
			if err != nil {
				return err
			}
			resp, err := c.Get(templateSubPath(args[0], "/context:resolve"), nil)
			return runJSON(rt, resp, err)
		},
	}
}

// --- template guardrails ------------------------------------------------------

func newTemplateGuardrails(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "guardrails",
		Short: "Manage guardrails scoped to one template",
		Long: `Template-scoped guardrails: allowlist rules, hooks, and network rules that
apply only to sessions launched from this template, layered on top of the
org-wide set. 'resolve' shows the merged, effective result including which
items were deduped or shadowed.

Types map as: allowlist -> allowlist_rule_v0, hook -> hook_v0,
network -> network_rule_v0. Content shapes match the org guardrail
directives; see 'runtm-api guardrails --help'.`,
	}
	cmd.AddCommand(
		newTemplateGuardrailsList(rt),
		newTemplateGuardrailsCreate(rt),
		newTemplateGuardrailsUpdate(rt),
		newTemplateGuardrailsDelete(rt),
		newTemplateGuardrailsResolve(rt),
	)
	return cmd
}

// guardrailTypeFor maps the CLI family shorthand to the stored type string.
func guardrailTypeFor(family string) (string, error) {
	switch family {
	case "allowlist":
		return "allowlist_rule_v0", nil
	case "hook":
		return "hook_v0", nil
	case "network":
		return "network_rule_v0", nil
	case "":
		return "", fmt.Errorf("--type is required (allowlist, hook, or network)")
	default:
		return "", fmt.Errorf("unsupported --type %q (expected allowlist, hook, or network)", family)
	}
}

func newTemplateGuardrailsList(rt *Runtime) *cobra.Command {
	var (
		family          string
		includeDisabled bool
		limit           int
		offset          int
	)
	cmd := &cobra.Command{
		Use:   "list <template_id>",
		Short: "List a template's guardrails (GET /api/org-templates/{id}/guardrails)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, "template guardrails")
			if err != nil {
				return err
			}
			q := url.Values{}
			if family != "" {
				q.Set("type_family", family)
			}
			if includeDisabled {
				q.Set("include_disabled", "true")
			}
			if limit > 0 {
				q.Set("limit", strconv.Itoa(limit))
			}
			if offset > 0 {
				q.Set("offset", strconv.Itoa(offset))
			}
			resp, err := c.Get(templateSubPath(args[0], "/guardrails"), q)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&family, "type", "", "Filter by family: allowlist, hook, or network")
	cmd.Flags().BoolVar(&includeDisabled, "include-disabled", false, "Include disabled guardrails")
	cmd.Flags().IntVar(&limit, "limit", 0, "Results per page (1-100)")
	cmd.Flags().IntVar(&offset, "offset", 0, "Pagination offset")
	return cmd
}

func newTemplateGuardrailsCreate(rt *Runtime) *cobra.Command {
	var (
		family      string
		name        string
		displayName string
		description string
		contentJSON string
		disabled    bool
		position    int
	)
	cmd := &cobra.Command{
		Use:   "create <template_id>",
		Short: "Add a guardrail to a template (POST /api/org-templates/{id}/guardrails)",
		Long: `Create a template-scoped guardrail. Content is the same shape as the org
guardrail directives:

  allowlist: '{"kind":"allow|ask|deny","pattern":"npm *","purpose":"..."}'
  hook:      '{"event":"PreToolUse","type":"command","script":"...","timeout":30}'
  network:   '{"kind":"host|cidr","value":"api.example.com","purpose":"..."}'

Example:
  runtm-api template guardrails create <template_id> --type allowlist \
    --name block-force-push --content '{"kind":"deny","pattern":"git push --force*"}'`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			typ, err := guardrailTypeFor(family)
			if err != nil {
				return err
			}
			if name == "" {
				return fmt.Errorf("--name is required")
			}
			content, err := parseJSONObject(contentJSON)
			if err != nil {
				return fmt.Errorf("--content: %w", err)
			}
			body := map[string]any{
				"type":    typ,
				"name":    name,
				"content": content,
				"enabled": !disabled,
			}
			if displayName != "" {
				body["display_name"] = displayName
			}
			if description != "" {
				body["description"] = description
			}
			if position > 0 {
				body["position"] = position
			}
			c, _, err := requireOrgClient(rt, "template guardrails")
			if err != nil {
				return err
			}
			resp, err := c.PostJSON(templateSubPath(args[0], "/guardrails"), body)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&family, "type", "", "Guardrail family: allowlist, hook, or network (required)")
	cmd.Flags().StringVar(&name, "name", "", "Guardrail name (required)")
	cmd.Flags().StringVar(&displayName, "display-name", "", "Human-readable name")
	cmd.Flags().StringVar(&description, "description", "", "Short description")
	cmd.Flags().StringVar(&contentJSON, "content", "", "Guardrail content object as JSON (required)")
	cmd.Flags().BoolVar(&disabled, "disabled", false, "Create disabled")
	cmd.Flags().IntVar(&position, "position", 0, "Ordering position (lower first)")
	_ = cmd.MarkFlagRequired("type")
	_ = cmd.MarkFlagRequired("name")
	_ = cmd.MarkFlagRequired("content")
	return cmd
}

func newTemplateGuardrailsUpdate(rt *Runtime) *cobra.Command {
	var (
		name        string
		displayName string
		description string
		contentJSON string
		enabled     bool
		disabled    bool
		position    int
	)
	cmd := &cobra.Command{
		Use:   "update <template_id> <guardrail_id>",
		Short: "Edit a template guardrail (PATCH /api/org-templates/{id}/guardrails/{gid})",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			body := map[string]any{}
			if cmd.Flags().Changed("name") {
				body["name"] = name
			}
			if cmd.Flags().Changed("display-name") {
				body["display_name"] = displayName
			}
			if cmd.Flags().Changed("description") {
				body["description"] = description
			}
			if cmd.Flags().Changed("content") {
				content, err := parseJSONObject(contentJSON)
				if err != nil {
					return fmt.Errorf("--content: %w", err)
				}
				body["content"] = content
			}
			if cmd.Flags().Changed("enabled") && cmd.Flags().Changed("disabled") {
				return fmt.Errorf("--enabled and --disabled are mutually exclusive")
			}
			if cmd.Flags().Changed("enabled") {
				body["enabled"] = enabled
			}
			if cmd.Flags().Changed("disabled") {
				body["enabled"] = !disabled
			}
			if cmd.Flags().Changed("position") {
				body["position"] = position
			}
			if len(body) == 0 {
				return fmt.Errorf("pass at least one field to update (--name, --display-name, --description, --content, --enabled/--disabled, --position)")
			}
			c, _, err := requireOrgClient(rt, "template guardrails")
			if err != nil {
				return err
			}
			path := templateSubPath(args[0], "/guardrails/"+url.PathEscape(args[1]))
			resp, err := c.PatchJSON(path, body)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&name, "name", "", "New name")
	cmd.Flags().StringVar(&displayName, "display-name", "", "New display name")
	cmd.Flags().StringVar(&description, "description", "", "New description")
	cmd.Flags().StringVar(&contentJSON, "content", "", "Replace the content object (JSON)")
	cmd.Flags().BoolVar(&enabled, "enabled", false, "Enable the guardrail")
	cmd.Flags().BoolVar(&disabled, "disabled", false, "Disable the guardrail")
	cmd.Flags().IntVar(&position, "position", 0, "Ordering position (lower first)")
	return cmd
}

func newTemplateGuardrailsDelete(rt *Runtime) *cobra.Command {
	var yes bool
	cmd := &cobra.Command{
		Use:   "delete <template_id> <guardrail_id>",
		Short: "Delete a template guardrail (DELETE /api/org-templates/{id}/guardrails/{gid})",
		Args:  cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			if !yes {
				rt.WriteObject(map[string]any{
					"error": "Destructive operation requires --yes to confirm.",
					"hint":  "Pass --yes when you are sure. To keep it but stop enforcing, use update --disabled.",
				})
				return errSilent
			}
			c, _, err := requireOrgClient(rt, "template guardrails")
			if err != nil {
				return err
			}
			resp, err := c.Delete(templateSubPath(args[0], "/guardrails/"+url.PathEscape(args[1])))
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().BoolVar(&yes, "yes", false, "Confirm deletion")
	return cmd
}

func newTemplateGuardrailsResolve(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "resolve <template_id>",
		Short: "The merged org + template guardrail set (GET .../guardrails:resolve)",
		Long: `Resolves what sessions from this template actually enforce: org items and
template items merged, each marked active, deduped, shadowed, or conflict,
plus the effective limits, allowlist default policy, and network egress.`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, "template guardrails")
			if err != nil {
				return err
			}
			resp, err := c.Get(templateSubPath(args[0], "/guardrails:resolve"), nil)
			return runJSON(rt, resp, err)
		},
	}
}
