package cmd

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"testing"
)

const multiFileSkill = `{"directive":{"id":"sk-1","name":"deploy-checks","description":"Checks","content":{
	"entry_md":"SKILL.md",
	"files":[
		{"path":"SKILL.md","mode":"text","inline":"old body"},
		{"path":"scripts/check.sh","mode":"text","ref":"sha256:abc","url":"https://storage/abc"}
	],
	"requires":{"integrations":["notion"]}}}}`

// skillServer answers GET with skill and records the PATCH body.
func skillServer(t *testing.T, skill string, patched *map[string]any) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.Method {
		case http.MethodGet:
			if r.URL.Query().Get("include_content") != "true" {
				t.Errorf("GET %s without include_content", r.URL)
			}
			fmt.Fprint(w, skill)
		case http.MethodPatch:
			_ = json.NewDecoder(r.Body).Decode(patched)
			fmt.Fprint(w, `{"directive":{}}`)
		}
	}
}

func writeMarkdown(t *testing.T, body string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "SKILL.md")
	if err := os.WriteFile(path, []byte(body), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func patchedFiles(t *testing.T, patched map[string]any) map[string]map[string]any {
	t.Helper()
	content, _ := patched["content"].(map[string]any)
	files := map[string]map[string]any{}
	for _, f := range content["files"].([]any) {
		file := f.(map[string]any)
		files[file["path"].(string)] = file
	}
	return files
}

func TestSkillsUpdateMdKeepsTheOtherFiles(t *testing.T) {
	var patched map[string]any
	rt, _ := shareRuntime(t, skillServer(t, multiFileSkill, &patched))
	t.Setenv("RUNTM_ORG_ID", "org_test")

	cmd := newSkillUpdate(rt)
	cmd.SetArgs([]string{"sk-1", "--md", writeMarkdown(t, "new body")})
	if err := cmd.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}

	files := patchedFiles(t, patched)
	if len(files) != 2 {
		t.Fatalf("files = %v, want SKILL.md and scripts/check.sh", files)
	}
	if files["SKILL.md"]["inline"] != "new body" {
		t.Errorf("SKILL.md = %v, want the new body", files["SKILL.md"])
	}
	if files["scripts/check.sh"]["ref"] != "sha256:abc" {
		t.Errorf("scripts/check.sh = %v, want its stored ref kept", files["scripts/check.sh"])
	}
	content := patched["content"].(map[string]any)
	if _, ok := content["requires"]; !ok {
		t.Errorf("requires dropped: %v", content)
	}
}

func TestSkillsUpdateMdReplacesTheSkillsOwnEntry(t *testing.T) {
	skill := `{"directive":{"id":"sk-1","content":{"entry_md":"README.md","files":[
		{"path":"README.md","mode":"text","inline":"old"},{"path":"lib.py","mode":"text","inline":"x"}]}}}`
	var patched map[string]any
	rt, _ := shareRuntime(t, skillServer(t, skill, &patched))
	t.Setenv("RUNTM_ORG_ID", "org_test")

	cmd := newSkillUpdate(rt)
	cmd.SetArgs([]string{"sk-1", "--md", writeMarkdown(t, "new")})
	if err := cmd.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}

	files := patchedFiles(t, patched)
	if _, ok := files["SKILL.md"]; ok || files["README.md"]["inline"] != "new" || len(files) != 2 {
		t.Errorf("files = %v, want README.md replaced and lib.py kept", files)
	}
}

func TestSkillsUpdateContentStillReplacesEverything(t *testing.T) {
	var patched map[string]any
	gets := 0
	rt, _ := shareRuntime(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet {
			gets++
		}
		skillServer(t, multiFileSkill, &patched)(w, r)
	})
	t.Setenv("RUNTM_ORG_ID", "org_test")

	cmd := newSkillUpdate(rt)
	cmd.SetArgs([]string{"sk-1", "--content", `{"entry_md":"SKILL.md","files":[{"path":"SKILL.md","inline":"only"}]}`})
	if err := cmd.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}

	if gets != 0 || len(patchedFiles(t, patched)) != 1 {
		t.Errorf("--content should replace the files without reading them (gets=%d, patched=%v)", gets, patched)
	}
}

func TestSkillsGetOutputWritesTheFiles(t *testing.T) {
	skill := `{"directive":{"id":"sk-1","name":"deploy-checks","description":"Checks","content":{
		"entry_md":"SKILL.md","files":[
			{"path":"SKILL.md","mode":"text","inline":"---\nname: deploy-checks\ndescription: Checks\n---\nbody"},
			{"path":"lib/a.txt","mode":"text","inline":"a"}]}}}`
	rt, stdout := shareRuntime(t, skillServer(t, skill, nil))
	t.Setenv("RUNTM_ORG_ID", "org_test")
	dir := t.TempDir()

	cmd := newSkillGet(rt)
	cmd.SetArgs([]string{"sk-1", "--output", dir})
	if err := cmd.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}

	got, err := os.ReadFile(filepath.Join(dir, "lib", "a.txt"))
	if err != nil || string(got) != "a" {
		t.Errorf("lib/a.txt = %q, %v", got, err)
	}
	if _, err := os.Stat(filepath.Join(dir, "SKILL.md")); err != nil {
		t.Errorf("SKILL.md not written: %v", err)
	}
	var out map[string]any
	if err := json.Unmarshal(stdout.Bytes(), &out); err != nil || out["path"] != dir {
		t.Errorf("output = %s, want the directory written", stdout)
	}
}
