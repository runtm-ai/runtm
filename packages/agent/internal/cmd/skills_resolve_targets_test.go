package cmd

import (
	"os"
	"path/filepath"
	"testing"
)

// resolveSkillTargets must detect an agent by its ROOT config dir (~/.claude),
// even when the skills/ subdir does not exist yet — MkdirAll creates it at
// install time. Regression: it previously required ~/.claude/skills to pre-exist,
// so `skills install` failed on a fresh agent that only had ~/.claude.
func TestResolveSkillTargetsDetectsAgentRootWithoutSkillsDir(t *testing.T) {
	home := t.TempDir()
	t.Setenv("HOME", home)
	// Agent present (settings/CLAUDE.md) but NO skills/ subdir.
	if err := os.MkdirAll(filepath.Join(home, ".claude"), 0o755); err != nil {
		t.Fatal(err)
	}

	got := resolveSkillTargets("")
	want := filepath.Join(home, ".claude/skills/runtm")
	if len(got) != 1 || got[0] != want {
		t.Fatalf("expected [%s], got %v", want, got)
	}
}

func TestResolveSkillTargetsEmptyWhenNoAgent(t *testing.T) {
	t.Setenv("HOME", t.TempDir())
	if got := resolveSkillTargets(""); len(got) != 0 {
		t.Fatalf("expected no targets, got %v", got)
	}
}

func TestResolveSkillTargetsOverride(t *testing.T) {
	if got := resolveSkillTargets("/tmp/custom"); len(got) != 1 || got[0] != "/tmp/custom" {
		t.Fatalf("override not honored, got %v", got)
	}
}
