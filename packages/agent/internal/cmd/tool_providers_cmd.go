package cmd

import (
	"encoding/json"
	"fmt"
	"net/url"
	"os"

	"github.com/spf13/cobra"
)

// Custom tool providers — the *definitions* behind tools (name, logo, the
// mise/github/npm package to install, and auth mechanism(s)), as opposed to
// `tools` (which stores credentials for a provider). Backed by
// /api/knowledge/providers; pure JSON, org-admin key with integrations:write.
//
// Wired into `runtm-api tools` as `tools providers …` in NewToolsCommand.

const knowledgeProvidersPath = "/knowledge/providers"

func providerItemPath(id string) string {
	return knowledgeProvidersPath + "/" + url.PathEscape(id)
}

func newToolProvidersCommand(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "providers",
		Short: "Define custom tool providers (package, auth, name, logo)",
		Long: `A custom provider defines a new tool: its name, logo, the package to
install in the sandbox (a mise/github/npm/cargo spec), and how to authenticate.
This is the org-owned definition behind a tool — separate from the credentials
that 'tools create' stores against it.

Org-scoped; writes need an admin/owner key with the integrations:write scope.
Pure API — no browser step (auth is declared in the schema; end users connect
credentials later).`,
	}
	cmd.AddCommand(
		newToolProviderList(rt),
		newToolProviderGet(rt),
		newToolProviderCreate(rt),
		newToolProviderUpdate(rt),
		newToolProviderDelete(rt),
		newToolProviderFork(rt),
	)
	return cmd
}

func newToolProviderList(rt *Runtime) *cobra.Command {
	var (
		pageSize  int
		pageToken string
	)
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List custom + built-in tool providers",
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, "tool providers")
			if err != nil {
				return err
			}
			resp, err := c.Get(knowledgeProvidersPath, listQuery(pageSize, pageToken))
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().IntVar(&pageSize, "page-size", 0, "Results per page (1-100)")
	cmd.Flags().StringVar(&pageToken, "page-token", "", "Pagination cursor")
	return cmd
}

func newToolProviderGet(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "get <id>",
		Short: "Get a tool provider (full schema)",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, "tool providers")
			if err != nil {
				return err
			}
			resp, err := c.Get(providerItemPath(args[0]), nil)
			return runJSON(rt, resp, err)
		},
	}
}

func newToolProviderCreate(rt *Runtime) *cobra.Command {
	var (
		slug             string
		name             string
		logo             string
		icon             string
		tagline          string
		description      string
		category         string
		packages         []string
		authMethodsJSON  string
		schemaJSON       string
		schemaFile       string
		oauthSecretsJSON string
	)
	cmd := &cobra.Command{
		Use:   "create",
		Short: "Create a custom tool provider",
		Long: `Create a custom provider. Build it from flags, or pass a full provider
schema with --schema / --schema-file (flags then override fields on top).

A provider needs a display name and at least one auth method.

  --package NAME=SPEC   binary to install (repeatable). SPEC is a mise spec:
                        latest | npm:pkg@1 | github:owner/repo | cargo:crate | ubi:owner/repo
  --auth-methods <json> the auth_methods array (static or oauth)

Example (static API key):
  runtm-api tools providers create \
    --slug internal-svc --name "Internal Service" \
    --logo https://example.com/logo.png --category data \
    --package curl=latest --package jq=latest \
    --auth-methods '[{"id":"api_key","display_name":"API Key","kind":"static",
      "fields":[{"id":"api_key","label":"API Key","kind":"secret"}],
      "materialization":{"env":{"INTERNAL_API_KEY":"{fields.api_key}"}}}]'`,
		RunE: func(cmd *cobra.Command, args []string) error {
			if slug == "" {
				return fmt.Errorf("--slug is required (^[a-z][a-z0-9_-]*$)")
			}
			schema, err := loadProviderSchema(schemaJSON, schemaFile)
			if err != nil {
				return err
			}
			if err := applyProviderSchemaFlags(cmd, schema, name, logo, icon, tagline, description, category, packages, authMethodsJSON); err != nil {
				return err
			}
			if _, ok := schema["display_name"]; !ok {
				return fmt.Errorf("provide --name (or display_name in --schema)")
			}
			if _, ok := schema["auth_methods"]; !ok {
				return fmt.Errorf("provide --auth-methods (or auth_methods in --schema); at least one is required")
			}

			body := map[string]any{"slug": slug, "schema": schema}
			if oauthSecretsJSON != "" {
				sec, perr := parseJSONObject(oauthSecretsJSON)
				if perr != nil {
					return fmt.Errorf("--oauth-secrets: %w", perr)
				}
				body["oauth_client_secrets"] = sec
			}

			c, _, err := requireOrgClient(rt, "tool providers")
			if err != nil {
				return err
			}
			resp, err := c.PostJSON(knowledgeProvidersPath, body)
			return runJSON(rt, resp, err)
		},
	}
	addProviderSchemaFlags(cmd, &name, &logo, &icon, &tagline, &description, &category, &packages, &authMethodsJSON, &schemaJSON, &schemaFile)
	cmd.Flags().StringVar(&slug, "slug", "", "Unique provider slug, ^[a-z][a-z0-9_-]*$ (required)")
	cmd.Flags().StringVar(&oauthSecretsJSON, "oauth-secrets", "", `Per-method OAuth client creds as JSON: {"method_id":{"client_id":"…","client_secret":"…"}}`)
	_ = cmd.MarkFlagRequired("slug")
	return cmd
}

func newToolProviderUpdate(rt *Runtime) *cobra.Command {
	var (
		name             string
		logo             string
		icon             string
		tagline          string
		description      string
		category         string
		packages         []string
		authMethodsJSON  string
		schemaJSON       string
		schemaFile       string
		oauthSecretsJSON string
	)
	cmd := &cobra.Command{
		Use:   "update <id>",
		Short: "Update a custom tool provider",
		Long: `Update a custom provider. The schema is replaced wholesale on the
backend, so by default we fetch the current schema, apply your flag changes,
and PATCH the result. Pass --schema / --schema-file to start from a full schema
instead of the current one. (Built-in/managed providers reject updates — fork
one first.)`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, "tool providers")
			if err != nil {
				return err
			}

			// Base schema: an explicit --schema/--schema-file, else the
			// provider's current schema (so flag edits are additive).
			var schema map[string]any
			if schemaJSON != "" || schemaFile != "" {
				schema, err = loadProviderSchema(schemaJSON, schemaFile)
				if err != nil {
					return err
				}
			} else {
				cur, gerr := c.Get(providerItemPath(args[0]), nil)
				if gerr != nil {
					return runJSON(rt, cur, gerr)
				}
				var wrap struct {
					Provider struct {
						Schema map[string]any `json:"schema"`
					} `json:"provider"`
				}
				if jerr := json.Unmarshal(cur, &wrap); jerr != nil {
					return fmt.Errorf("could not read current provider schema: %w", jerr)
				}
				schema = wrap.Provider.Schema
				if schema == nil {
					schema = map[string]any{}
				}
			}

			if err := applyProviderSchemaFlags(cmd, schema, name, logo, icon, tagline, description, category, packages, authMethodsJSON); err != nil {
				return err
			}

			body := map[string]any{"schema": schema}
			if oauthSecretsJSON != "" {
				sec, perr := parseJSONObject(oauthSecretsJSON)
				if perr != nil {
					return fmt.Errorf("--oauth-secrets: %w", perr)
				}
				body["oauth_client_secrets"] = sec
			}
			resp, err := c.PatchJSON(providerItemPath(args[0]), body)
			return runJSON(rt, resp, err)
		},
	}
	addProviderSchemaFlags(cmd, &name, &logo, &icon, &tagline, &description, &category, &packages, &authMethodsJSON, &schemaJSON, &schemaFile)
	cmd.Flags().StringVar(&oauthSecretsJSON, "oauth-secrets", "", "Replace per-method OAuth client creds (JSON)")
	return cmd
}

func newToolProviderDelete(rt *Runtime) *cobra.Command {
	var yes bool
	cmd := &cobra.Command{
		Use:   "delete <id>",
		Short: "Delete a custom tool provider",
		Args:  cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if !yes {
				rt.WriteObject(map[string]any{
					"error": "Destructive operation requires --yes to confirm.",
					"hint":  "Pass --yes when you are sure.",
				})
				return errSilent
			}
			c, _, err := requireOrgClient(rt, "tool providers")
			if err != nil {
				return err
			}
			resp, err := c.Delete(providerItemPath(args[0]))
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().BoolVar(&yes, "yes", false, "Confirm deletion")
	return cmd
}

func newToolProviderFork(rt *Runtime) *cobra.Command {
	var slug string
	cmd := &cobra.Command{
		Use:   "fork <id>",
		Short: "Fork a built-in provider into an editable custom copy",
		Long: `Copy a managed/built-in provider into an editable org-owned provider —
e.g. to bring your own OAuth app. The new provider is pre-populated with the
built-in's schema under the slug you give.`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if slug == "" {
				return fmt.Errorf("--slug is required for the forked copy")
			}
			c, _, err := requireOrgClient(rt, "tool providers")
			if err != nil {
				return err
			}
			resp, err := c.PostJSON(providerItemPath(args[0])+":fork", map[string]any{"slug": slug})
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&slug, "slug", "", "Slug for the forked custom provider (required)")
	_ = cmd.MarkFlagRequired("slug")
	return cmd
}

// --- shared schema helpers --------------------------------------------------

func addProviderSchemaFlags(cmd *cobra.Command, name, logo, icon, tagline, description, category *string, packages *[]string, authMethodsJSON, schemaJSON, schemaFile *string) {
	cmd.Flags().StringVar(name, "name", "", "Display name")
	cmd.Flags().StringVar(logo, "logo", "", "Logo URL (schema.image_url)")
	cmd.Flags().StringVar(icon, "icon", "", "Icon key (schema.icon)")
	cmd.Flags().StringVar(tagline, "tagline", "", "Short tagline")
	cmd.Flags().StringVar(description, "description", "", "Description")
	cmd.Flags().StringVar(category, "category", "", "Category (documents, data, observability, project_management, crm, support, messaging, meetings, sales, custom)")
	cmd.Flags().StringArrayVar(packages, "package", nil, "Package to install as NAME=SPEC (repeatable). SPEC = latest | npm:pkg | github:owner/repo | cargo:crate | ubi:owner/repo")
	cmd.Flags().StringVar(authMethodsJSON, "auth-methods", "", "auth_methods array as JSON (static/oauth)")
	cmd.Flags().StringVar(schemaJSON, "schema", "", "Full provider schema as JSON (flags override on top)")
	cmd.Flags().StringVar(schemaFile, "schema-file", "", "Path to a JSON file with the full provider schema")
}

// loadProviderSchema returns the base schema map from --schema / --schema-file,
// or an empty map when neither is set.
func loadProviderSchema(schemaJSON, schemaFile string) (map[string]any, error) {
	if schemaFile != "" {
		data, err := os.ReadFile(schemaFile)
		if err != nil {
			return nil, fmt.Errorf("read --schema-file: %w", err)
		}
		var m map[string]any
		if err := json.Unmarshal(data, &m); err != nil {
			return nil, fmt.Errorf("--schema-file is not a JSON object: %w", err)
		}
		return m, nil
	}
	if schemaJSON != "" {
		return parseJSONObject(schemaJSON)
	}
	return map[string]any{}, nil
}

// applyProviderSchemaFlags layers the convenience flags onto a base schema map.
func applyProviderSchemaFlags(cmd *cobra.Command, schema map[string]any, name, logo, icon, tagline, description, category string, packages []string, authMethodsJSON string) error {
	if cmd.Flags().Changed("name") {
		schema["display_name"] = name
	}
	if cmd.Flags().Changed("logo") {
		schema["image_url"] = logo
	}
	if cmd.Flags().Changed("icon") {
		schema["icon"] = icon
	}
	if cmd.Flags().Changed("tagline") {
		schema["tagline"] = tagline
	}
	if cmd.Flags().Changed("description") {
		schema["description"] = description
	}
	if cmd.Flags().Changed("category") {
		schema["category"] = category
	}
	if len(packages) > 0 {
		mise := map[string]any{}
		// Preserve any existing tooling.mise entries from the base schema.
		if tooling, ok := schema["tooling"].(map[string]any); ok {
			if existing, ok := tooling["mise"].(map[string]any); ok {
				mise = existing
			}
		}
		pkgs, err := parseKeyVals(packages)
		if err != nil {
			return err
		}
		for k, v := range pkgs {
			mise[k] = v
		}
		schema["tooling"] = map[string]any{"mise": mise}
	}
	if cmd.Flags().Changed("auth-methods") {
		var methods any
		if err := json.Unmarshal([]byte(authMethodsJSON), &methods); err != nil {
			return fmt.Errorf("--auth-methods is not valid JSON: %w", err)
		}
		schema["auth_methods"] = methods
	}
	return nil
}
