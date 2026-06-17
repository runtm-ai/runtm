package cmd

import (
	"fmt"
	"os"
	"path/filepath"

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

Cloud commands (create/get/list/update/delete) are org-scoped: pass --org or
RUNTM_ORG_ID, or use an org-scoped key. Writes need the context:write scope.

'skills install' is a local operation (no API call) that copies the skill files
embedded in this binary into the detected agent's skills directory.`,
	}
	cmd.AddCommand(
		newSkillsList(rt),
		newDirectiveGet(rt, "skill"),
		newSkillCreate(rt),
		newSkillUpdate(rt),
		newDirectiveDelete(rt, "skill"),
		newDirectiveAttachments(rt, "skill"),
		newDirectiveAttach(rt, "skill"),
		newDirectiveDetach(rt, "skill"),
		newSkillsInstall(rt),
		newSkillsPull(rt),
	)
	return cmd
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
				rt.WriteObject(map[string]any{
					"installed": false,
					"error":     "No AI agent skills directory found (~/.claude or ~/.cursor).",
					"hint":      "Pass --target <dir> to install manually, or create ~/.claude/skills/ first.",
				})
				return errSilent
			}

			files, err := listEmbeddedSkills()
			if err != nil {
				return fmt.Errorf("read embedded skills: %w", err)
			}

			installed := []map[string]any{}
			for _, dir := range targets {
				if err := os.MkdirAll(dir, 0o755); err != nil {
					return fmt.Errorf("mkdir %s: %w", dir, err)
				}
				for _, f := range files {
					data, err := skills.FS.ReadFile(skills.Dir + "/" + f)
					if err != nil {
						return fmt.Errorf("read embedded %s: %w", f, err)
					}
					dest := filepath.Join(dir, f)
					if err := os.WriteFile(dest, data, 0o644); err != nil {
						return fmt.Errorf("write %s: %w", dest, err)
					}
				}
				installed = append(installed, map[string]any{
					"directory": dir,
					"files":     files,
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
		pageSize       int
		pageToken      string
		includeContent bool
	)
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List your org's skills (or --bundled for this CLI's own skills)",
		RunE: func(cmd *cobra.Command, args []string) error {
			// --bundled: list the skill files embedded in this binary (local).
			if bundled {
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
			if includeContent {
				q.Set("include_content", "true")
			}
			resp, err := c.Get(directivesListPath, q)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().BoolVar(&bundled, "bundled", false, "List the skill files embedded in this CLI binary instead of org skills")
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

	var targets []string
	for _, rel := range []string{
		".claude/skills/runtm",
		".cursor/skills/runtm",
	} {
		dir := filepath.Join(home, rel)
		parent := filepath.Dir(dir)
		if info, err := os.Stat(parent); err == nil && info.IsDir() {
			targets = append(targets, dir)
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
