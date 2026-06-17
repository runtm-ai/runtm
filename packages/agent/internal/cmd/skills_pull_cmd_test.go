package cmd

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestEnsureSkillFrontmatterPreservesExisting(t *testing.T) {
	md := "---\nname: deploy-checks\ndescription: Run pre-deploy checks\n---\n\nBody here.\n"
	s := pulledSkill{Name: "other", Description: "other desc"}
	got := ensureSkillFrontmatter(s, &pulledSkillContent{}, md)
	if got != md {
		t.Fatalf("expected unchanged markdown, got:\n%s", got)
	}
}

func TestEnsureSkillFrontmatterSynthesizesFromMetadata(t *testing.T) {
	// No frontmatter at all; name/description come from the directive.
	md := "Just the body, no frontmatter.\n"
	s := pulledSkill{Name: "deploy-checks", Description: "Run pre-deploy checks"}
	got := ensureSkillFrontmatter(s, &pulledSkillContent{}, md)

	if !strings.HasPrefix(got, "---\n") {
		t.Fatalf("expected frontmatter block, got:\n%s", got)
	}
	if !strings.Contains(got, "name: deploy-checks") {
		t.Errorf("missing synthesized name:\n%s", got)
	}
	if !strings.Contains(got, "description: Run pre-deploy checks") {
		t.Errorf("missing synthesized description:\n%s", got)
	}
	if !strings.Contains(got, "Just the body, no frontmatter.") {
		t.Errorf("body dropped:\n%s", got)
	}
}

func TestEnsureSkillFrontmatterFallsBackToStoredFrontmatter(t *testing.T) {
	md := "Body only.\n"
	s := pulledSkill{Name: "x"}
	content := &pulledSkillContent{Frontmatter: map[string]any{
		"name":        "from-store",
		"description": "stored description",
	}}
	got := ensureSkillFrontmatter(s, content, md)
	if !strings.Contains(got, "name: from-store") || !strings.Contains(got, "description: stored description") {
		t.Fatalf("expected stored frontmatter to win over directive name, got:\n%s", got)
	}
}

func TestCleanRelPathNeutralizesTraversal(t *testing.T) {
	cases := map[string]string{
		"SKILL.md":         "SKILL.md",
		"./SKILL.md":       "SKILL.md",
		"lib/helper.py":    filepath.FromSlash("lib/helper.py"),
		"../../etc/passwd": filepath.FromSlash("etc/passwd"),
		"a/../../../b.txt": "b.txt",
	}
	for in, want := range cases {
		if got := cleanRelPath(in); got != want {
			t.Errorf("cleanRelPath(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestSanitizeSkillName(t *testing.T) {
	cases := map[string]string{
		"deploy-checks": "deploy-checks",
		"a/b":           "a-b",
		"../evil":       "--evil",
		"":              "skill",
	}
	for in, want := range cases {
		if got := sanitizeSkillName(in); got != want {
			t.Errorf("sanitizeSkillName(%q) = %q, want %q", in, got, want)
		}
	}
}

func TestResolvePullTargets(t *testing.T) {
	home, err := os.UserHomeDir()
	if err != nil {
		t.Fatalf("UserHomeDir: %v", err)
	}
	claude := filepath.Join(home, ".claude", "skills")
	codex := filepath.Join(home, ".agents", "skills")

	// Default: claude-code only.
	got, err := resolvePullTargets("", nil)
	if err != nil {
		t.Fatalf("default: %v", err)
	}
	if len(got) != 1 || got[0] != claude {
		t.Errorf("default targets = %v, want [%s]", got, claude)
	}

	// all → both roots, deduped and ordered.
	got, err = resolvePullTargets("", []string{"all"})
	if err != nil {
		t.Fatalf("all: %v", err)
	}
	if len(got) != 2 || got[0] != claude || got[1] != codex {
		t.Errorf("all targets = %v, want [%s %s]", got, claude, codex)
	}

	// Repeated/overlapping agents dedupe to the same root set.
	got, _ = resolvePullTargets("", []string{"claude-code", "codex", "claude"})
	if len(got) != 2 || got[0] != claude || got[1] != codex {
		t.Errorf("multi-agent targets = %v, want [%s %s]", got, claude, codex)
	}

	// Explicit --target wins, verbatim.
	got, _ = resolvePullTargets("/tmp/x", []string{"all"})
	if len(got) != 1 || got[0] != "/tmp/x" {
		t.Errorf("override targets = %v, want [/tmp/x]", got)
	}

	if _, err := resolvePullTargets("", []string{"bogus"}); err == nil {
		t.Error("expected error for invalid --agent")
	}
}

func TestFetchTemplateSkillsPaginates(t *testing.T) {
	page := 0
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.URL.Query().Get("template_id"); got != "tmpl_1" {
			t.Errorf("template_id = %q, want tmpl_1", got)
		}
		if got := r.URL.Query().Get("type_family"); got != "skill" {
			t.Errorf("type_family = %q, want skill", got)
		}
		if r.URL.Query().Get("include_content") != "true" {
			t.Errorf("expected include_content=true")
		}
		w.Header().Set("Content-Type", "application/json")
		if page == 0 {
			page++
			_, _ = w.Write([]byte(`{"directives":[{"id":"d1","name":"one","content":{"entry_md":"SKILL.md","files":[]}}],"next_page_token":"tok2","total_size":2}`))
			return
		}
		_, _ = w.Write([]byte(`{"directives":[{"id":"d2","name":"two","content":{"entry_md":"SKILL.md","files":[]}}],"next_page_token":"","total_size":2}`))
	}))
	defer srv.Close()

	skills, err := fetchTemplateSkills(newTestClient(srv.URL), "tmpl_1", nil)
	if err != nil {
		t.Fatalf("fetchTemplateSkills: %v", err)
	}
	if len(skills) != 2 || skills[0].Name != "one" || skills[1].Name != "two" {
		t.Fatalf("unexpected skills: %+v", skills)
	}
}

func TestFetchTemplateSkillsMergesScopesAndDedupes(t *testing.T) {
	var scopes []string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		q := r.URL.Query()
		w.Header().Set("Content-Type", "application/json")
		switch {
		case q.Get("template_id") == "tmpl_1":
			scopes = append(scopes, "template")
			// applies_to_all (d0) + template-scoped (d1)
			_, _ = w.Write([]byte(`{"directives":[{"id":"d0","name":"org-wide","content":{"files":[]}},{"id":"d1","name":"tmpl","content":{"files":[]}}],"next_page_token":"","total_size":2}`))
		case q.Get("repo_full_name") == "acme/api":
			scopes = append(scopes, "repo")
			// applies_to_all again (d0, dup) + repo-scoped (d2)
			_, _ = w.Write([]byte(`{"directives":[{"id":"d0","name":"org-wide","content":{"files":[]}},{"id":"d2","name":"repo","content":{"files":[]}}],"next_page_token":"","total_size":2}`))
		default:
			t.Errorf("unexpected query: %v", q)
			_, _ = w.Write([]byte(`{"directives":[],"next_page_token":"","total_size":0}`))
		}
	}))
	defer srv.Close()

	skills, err := fetchTemplateSkills(newTestClient(srv.URL), "tmpl_1", []string{"acme/api", ""})
	if err != nil {
		t.Fatalf("fetchTemplateSkills: %v", err)
	}
	// d0 deduped across both scopes; order preserved by first sighting.
	if len(skills) != 3 {
		t.Fatalf("expected 3 deduped skills, got %d: %+v", len(skills), skills)
	}
	want := []string{"org-wide", "tmpl", "repo"}
	for i, name := range want {
		if skills[i].Name != name {
			t.Errorf("skills[%d] = %q, want %q", i, skills[i].Name, name)
		}
	}
	if len(scopes) != 2 || scopes[0] != "template" || scopes[1] != "repo" {
		t.Errorf("expected template then repo scope calls, got %v", scopes)
	}
}

func TestWriteSkillWritesEntryAndExtraFiles(t *testing.T) {
	body := "Body text."
	helper := "print('hi')"
	s := pulledSkill{
		Name:        "deploy-checks",
		Description: "Run pre-deploy checks",
		Content: &pulledSkillContent{
			EntryMd: "SKILL.md",
			Files: []pulledSkillFile{
				{Path: "SKILL.md", Mode: "text", Inline: &body},
				{Path: "lib/helper.py", Mode: "text", Inline: &helper},
			},
		},
	}

	dir := t.TempDir()
	skillDir := filepath.Join(dir, sanitizeSkillName(s.Name))
	written, err := writeSkill(nil, s, skillDir)
	if err != nil {
		t.Fatalf("writeSkill: %v", err)
	}
	if len(written) != 2 {
		t.Fatalf("expected 2 files written, got %v", written)
	}

	entry, err := os.ReadFile(filepath.Join(skillDir, "SKILL.md"))
	if err != nil {
		t.Fatalf("read entry: %v", err)
	}
	if !strings.Contains(string(entry), "name: deploy-checks") {
		t.Errorf("entry missing synthesized frontmatter:\n%s", entry)
	}
	if !strings.Contains(string(entry), "Body text.") {
		t.Errorf("entry missing body:\n%s", entry)
	}

	got, err := os.ReadFile(filepath.Join(skillDir, "lib", "helper.py"))
	if err != nil {
		t.Fatalf("read helper: %v", err)
	}
	if string(got) != helper {
		t.Errorf("helper.py = %q, want %q", got, helper)
	}
}
