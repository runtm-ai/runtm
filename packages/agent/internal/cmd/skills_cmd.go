package cmd

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/runtm-ai/runtm/packages/agent/internal/skills"
	"github.com/spf13/cobra"
)

// NewSkillsCommand returns `runtm skills` with install/list subcommands.
func NewSkillsCommand(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "skills",
		Short: "Install agent skill files for Claude Code, Cursor, etc.",
	}
	cmd.AddCommand(newSkillsInstall(rt), newSkillsList(rt))
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
					"files":    files,
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
	return &cobra.Command{
		Use:   "list",
		Short: "List the skill files embedded in this binary",
		RunE: func(cmd *cobra.Command, args []string) error {
			files, err := listEmbeddedSkills()
			if err != nil {
				return err
			}
			rt.WriteObject(map[string]any{
				"skills": files,
				"count":  len(files),
			})
			return nil
		},
	}
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
