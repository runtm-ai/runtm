package cmd

import (
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"

	"github.com/runtm-ai/runtm/packages/agent/internal/client"
	"github.com/runtm-ai/runtm/packages/agent/internal/skills"
	"github.com/spf13/cobra"
)

// NewSkillsCommand returns `runtm-api skills`. It covers two things:
//
//   - cloud CRUD: create/get/list/update/delete skills in your org
//   - install: write this CLI's own bundled skill files to ~/.claude / ~/.cursor
//
// `skills list` lists your org's skills; add --bundled to instead list the
// skill files embedded in this binary.
func NewSkillsCommand(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "skills",
		Short: "Create and manage skills (and install the bundled CLI skills)",
		Long: `Create, list, update, and delete your org's skills, and install this
CLI's own bundled skill files locally.

Cloud commands (create/get/list/update/delete) are org-scoped: they require an
org-scoped API key, which --org / RUNTM_ORG_ID cannot substitute for. Writes
need the context:write scope.

'skills install' is a local operation (no API call) that copies the skill files
embedded in this binary into the detected agent's skills directory.`,
	}
	cmd.AddCommand(
		newSkillsList(rt),
		newSkillGet(rt),
		newSkillCreate(rt),
		newSkillUpdate(rt),
		newDirectiveDelete(rt, "skill"),
		newDirectiveAttachments(rt, "skill"),
		newDirectiveAttach(rt, "skill"),
		newDirectiveDetach(rt, "skill"),
		newDirectiveImport(rt, "skill"),
		newDirectiveDiscover(rt),
		newDirectiveResync(rt, "skill"),
		newDirectiveLock(rt, "skill", true),
		newDirectiveLock(rt, "skill", false),
		newDirectiveFacets(rt, "skill", "skill"),
		newDirectiveUploadFile(rt, "skill"),
		newSkillsInstall(rt),
		newSkillsPull(rt),
	)
	return cmd
}

// newSkillGet is the shared get plus --output: the links it prints for files
// in object storage expire in minutes, so a copy that lasts has to be the files.
func newSkillGet(rt *Runtime) *cobra.Command {
	var output string
	cmd := newDirectiveGet(rt, "skill")
	printJSON := cmd.RunE
	cmd.RunE = func(cmd *cobra.Command, args []string) error {
		if output == "" {
			return printJSON(cmd, args)
		}
		c, _, err := requireOrgClient(rt, "skills")
		if err != nil {
			return err
		}
		var s pulledSkill
		if err := getSkill(c, args[0], &s); err != nil {
			return err
		}
		files, err := writeSkill(c, s, output)
		if err != nil {
			return fmt.Errorf("write skill %q: %w", s.Name, err)
		}
		rt.WriteObject(map[string]any{"skill": s.Name, "id": s.ID, "path": output, "files": files})
		return nil
	}
	cmd.Flags().StringVar(&output, "output", "", "Write the skill's files into this directory instead of printing links that expire")
	return cmd
}

// getSkill decodes skill id, content included, into v.
func getSkill(c *client.Client, id string, v any) error {
	q := url.Values{}
	q.Set("include_content", "true")
	raw, err := c.Get(directivePath(id), q)
	if err != nil {
		return err
	}
	var resp struct {
		Directive json.RawMessage `json:"directive"`
	}
	if err := json.Unmarshal(raw, &resp); err != nil {
		return fmt.Errorf("parse skill: %w", err)
	}
	if len(resp.Directive) == 0 {
		return fmt.Errorf("no skill %s in the response", id)
	}
	return json.Unmarshal(resp.Directive, v)
}

// updatedSkillContent is the content `skills update` sends: --content replaces
// all of it, --md only the entry file, keeping the skill's other files.
func updatedSkillContent(c *client.Client, id, mdPath, entry, contentJSON string) (map[string]any, error) {
	if contentJSON != "" || mdPath == "" {
		return resolveSkillContent(mdPath, entry, contentJSON)
	}
	var current struct {
		Content map[string]any `json:"content"`
	}
	if err := getSkill(c, id, &current); err != nil {
		return nil, err
	}
	if current.Content == nil {
		return nil, fmt.Errorf("skill %s returned no content to keep; pass --content to replace it whole", id)
	}
	if entry == "" {
		entry, _ = current.Content["entry_md"].(string)
	}
	md, err := resolveSkillContent(mdPath, entry, "")
	if err != nil {
		return nil, err
	}
	return withEntryFile(current.Content, md), nil
}

// withEntryFile returns content with md's single entry file in place of its own.
func withEntryFile(content, md map[string]any) map[string]any {
	entry, _ := md["entry_md"].(string)
	mdFiles, _ := md["files"].([]map[string]any)
	files := make([]any, 0, len(mdFiles))
	for _, f := range mdFiles {
		files = append(files, f)
	}
	existing, _ := content["files"].([]any)
	for _, f := range existing {
		if file, ok := f.(map[string]any); ok && strings.TrimPrefix(fmt.Sprint(file["path"]), "./") == strings.TrimPrefix(entry, "./") {
			continue
		}
		files = append(files, f)
	}
	merged := make(map[string]any, len(content)+1)
	for k, v := range content {
		merged[k] = v
	}
	merged["entry_md"] = entry
	merged["files"] = files
	return merged
}

func newSkillsInstall(rt *Runtime) *cobra.Command {
	var target string
	cmd := &cobra.Command{
		Use:   "install",
		Short: "Install SKILL.md files to the detected AI agent's skills directory",
		Long: `Auto-detects Claude Code (~/.claude/skills/runtm/) and Cursor
(~/.cursor/skills/runtm/) and copies the embedded skill files there.

Pass --target to override the destination directory.`,
		RunE: func(cmd *cobra.Command, args []string) error {
			targets := resolveSkillTargets(target)
			if len(targets) == 0 {
				// No agent config dir detected. `skills install` is an explicit
				// setup command, so don't bail — default to Claude Code's location
				// and create it (the install loop's MkdirAll does the `mkdir -p`).
				home, err := os.UserHomeDir()
				if err != nil {
					rt.WriteObject(map[string]any{
						"installed": false,
						"error":     "Could not resolve home directory.",
						"hint":      "Pass --target <dir> to install manually.",
					})
					return errSilent
				}
				targets = []string{filepath.Join(home, ".claude/skills/runtm")}
			}

			files, err := listEmbeddedSkills()
			if err != nil {
				return fmt.Errorf("read embedded skills: %w", err)
			}

			installed := []map[string]any{}
			for _, dir := range targets {
				if err := os.MkdirAll(dir, 0o750); err != nil {
					return fmt.Errorf("mkdir %s: %w", dir, err)
				}
				for _, f := range files {
					data, err := skills.FS.ReadFile(skills.Dir + "/" + f)
					if err != nil {
						return fmt.Errorf("read embedded %s: %w", f, err)
					}
					dest := filepath.Join(dir, f)
					if err := os.WriteFile(dest, data, 0o600); err != nil {
						return fmt.Errorf("write %s: %w", dest, err)
					}
				}
				// Earlier releases shipped sibling recipe files (runtm-sessions.md,
				// runtm-templates.md, ...) that now live in the docs. Remove any
				// runtm-*.md the binary no longer embeds so the installed skill is
				// exactly what this version ships, and nothing stale is loaded.
				removed := pruneLegacySkillFiles(dir, files)
				installed = append(installed, map[string]any{
					"directory": dir,
					"files":     files,
					"removed":   removed,
				})
			}

			rt.WriteObject(map[string]any{
				"installed": true,
				"targets":   installed,
			})
			return nil
		},
	}
	cmd.Flags().StringVar(&target, "target", "", "Override install directory (skip auto-detection)")
	return cmd
}

func newSkillsList(rt *Runtime) *cobra.Command {
	var (
		bundled        bool
		templateID     string
		repo           string
		pageSize       int
		pageToken      string
		includeContent bool
	)
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List your org's skills (--template to scope to one template)",
		Long: `List the skills in your org.

  --template <id>   only the skills a session from that template loads.
                    Equivalent to 'runtm-api template skills <id>' — the same
                    answer from whichever command you reached for first.
  --repo owner/name only the skills attached to that repo.
  --bundled         the skill files embedded in this binary (local, no API).

Both scoped forms include org-wide skills (those attached with --all), because
those load too. 'runtm-api template get <id>' also carries the resolved list
inline, along with whether it is stale relative to the last build.`,
		RunE: func(cmd *cobra.Command, args []string) error {
			// --bundled: list the skill files embedded in this binary (local).
			if bundled {
				if templateID != "" || repo != "" {
					return fmt.Errorf("--bundled lists this binary's own files; it cannot be combined with --template or --repo")
				}
				files, err := listEmbeddedSkills()
				if err != nil {
					return err
				}
				rt.WriteObject(map[string]any{
					"skills": files,
					"count":  len(files),
				})
				return nil
			}
			// Default: list the org's cloud skills.
			c, _, err := requireOrgClient(rt, "skills")
			if err != nil {
				return err
			}
			q := listQuery(pageSize, pageToken)
			q.Set("type_family", "skill")
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
	cmd.Flags().BoolVar(&bundled, "bundled", false, "List the skill files embedded in this CLI binary instead of org skills")
	cmd.Flags().StringVar(&templateID, "template", "", "Only skills a session from this template loads (same as 'template skills <id>')")
	cmd.Flags().StringVar(&repo, "repo", "", "Only skills attached to this repo (owner/name)")
	cmd.Flags().IntVar(&pageSize, "page-size", 0, "Results per page (1-100)")
	cmd.Flags().StringVar(&pageToken, "page-token", "", "Pagination cursor")
	cmd.Flags().BoolVar(&includeContent, "include-content", false, "Include each skill's content payload")
	return cmd
}

func resolveSkillTargets(override string) []string {
	if override != "" {
		return []string{override}
	}

	home, err := os.UserHomeDir()
	if err != nil {
		return nil
	}

	// Detect each agent by its ROOT config dir (~/.claude, ~/.cursor) — if the
	// agent is present, install into <root>/skills/runtm (created on demand by
	// MkdirAll at install time). Checking for <root>/skills to pre-exist was too
	// strict: a fresh agent has ~/.claude (settings.json/CLAUDE.md) but no
	// skills/ subdir yet, so `skills install` wrongly reported "no agent found".
	var targets []string
	for _, a := range []struct{ root, target string }{
		{".claude", ".claude/skills/runtm"},
		{".cursor", ".cursor/skills/runtm"},
	} {
		if info, err := os.Stat(filepath.Join(home, a.root)); err == nil && info.IsDir() {
			targets = append(targets, filepath.Join(home, a.target))
		}
	}
	return targets
}

func listEmbeddedSkills() ([]string, error) {
	entries, err := skills.FS.ReadDir(skills.Dir)
	if err != nil {
		return nil, err
	}
	var names []string
	for _, e := range entries {
		if !e.IsDir() {
			names = append(names, e.Name())
		}
	}
	return names, nil
}

// pruneLegacySkillFiles deletes runtm-*.md files in dir that are not part of
// the embedded set. Only the legacy sibling-skill naming pattern is touched so
// a user's own files in the directory are never removed.
func pruneLegacySkillFiles(dir string, embedded []string) []string {
	keep := make(map[string]struct{}, len(embedded))
	for _, f := range embedded {
		keep[f] = struct{}{}
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil
	}
	removed := []string{}
	for _, e := range entries {
		name := e.Name()
		if e.IsDir() || !strings.HasPrefix(name, "runtm-") || !strings.HasSuffix(name, ".md") {
			continue
		}
		if _, ok := keep[name]; ok {
			continue
		}
		if err := os.Remove(filepath.Join(dir, name)); err == nil {
			removed = append(removed, name)
		}
	}
	return removed
}
