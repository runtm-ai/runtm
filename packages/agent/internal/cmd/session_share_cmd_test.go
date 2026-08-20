package cmd

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// shareRuntime wires a Runtime at a mock API so the share commands can be
// executed end to end (flags -> request -> printed JSON) rather than only
// checked for registration.
func shareRuntime(t *testing.T, handler http.HandlerFunc) (*Runtime, *bytes.Buffer) {
	t.Helper()
	srv := httptest.NewServer(handler)
	t.Cleanup(srv.Close)
	t.Setenv("RUNTM_API_KEY", "runtm_sk_test")
	t.Setenv("RUNTM_ORG_ID", "")
	stdout := &bytes.Buffer{}
	return &Runtime{
		Flags:  &GlobalFlags{APIURL: srv.URL},
		Stdout: stdout,
		Stderr: &bytes.Buffer{},
	}, stdout
}

func TestSessionShareSubcommandsRegistered(t *testing.T) {
	root := NewRootCommand()
	session := findSub(t, root, "session")

	if !hasSub(session, "share") {
		t.Fatal("session is missing \"share\"")
	}
	share := findSub(t, session, "share")
	for _, name := range []string{"create", "list", "revoke"} {
		if !hasSub(share, name) {
			t.Errorf("session share is missing %q", name)
		}
	}
	// The preview listing is a sibling of share, not a child.
	if !hasSub(session, "previews") {
		t.Error("session is missing \"previews\"")
	}
}

func TestShareCreateSendsEmailAndPort(t *testing.T) {
	var gotPath, gotMethod string
	var gotBody map[string]any

	rt, stdout := shareRuntime(t, func(w http.ResponseWriter, r *http.Request) {
		gotPath, gotMethod = r.URL.Path, r.Method
		_ = json.NewDecoder(r.Body).Decode(&gotBody)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"created":true,"emailed":true,"preview_url":"https://3000-x.dev.runtm.com"}`))
	})

	cmd := newSessionShareCreate(rt)
	cmd.SetArgs([]string{"ses-1", "--email", "dev@acme.com", "--port", "5173"})
	if err := cmd.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}

	if gotMethod != http.MethodPost {
		t.Errorf("method = %q, want POST", gotMethod)
	}
	if !strings.HasSuffix(gotPath, "/sessions/ses-1/preview-shares") {
		t.Errorf("path = %q", gotPath)
	}
	if gotBody["email"] != "dev@acme.com" {
		t.Errorf("email = %v", gotBody["email"])
	}
	if gotBody["port"].(float64) != 5173 {
		t.Errorf("port = %v, want 5173", gotBody["port"])
	}
	// preview_url is omitted unless explicitly overridden — the server derives
	// it, which is the whole point for a CLI caller.
	if _, ok := gotBody["preview_url"]; ok {
		t.Error("preview_url should be omitted when not passed")
	}
	// The printed body must carry the link, since that's what the user sends
	// when email delivery isn't configured.
	if !strings.Contains(stdout.String(), "3000-x.dev.runtm.com") {
		t.Errorf("stdout missing preview_url: %s", stdout.String())
	}
}

func TestShareCreateDefaultsToPort3000(t *testing.T) {
	var gotBody map[string]any
	rt, _ := shareRuntime(t, func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewDecoder(r.Body).Decode(&gotBody)
		_, _ = w.Write([]byte(`{}`))
	})

	cmd := newSessionShareCreate(rt)
	cmd.SetArgs([]string{"ses-1", "--email", "dev@acme.com"})
	if err := cmd.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}
	if gotBody["port"].(float64) != 3000 {
		t.Errorf("default port = %v, want 3000", gotBody["port"])
	}
}

func TestShareCreateRequiresEmail(t *testing.T) {
	called := false
	rt, _ := shareRuntime(t, func(w http.ResponseWriter, r *http.Request) {
		called = true
		_, _ = w.Write([]byte(`{}`))
	})

	cmd := newSessionShareCreate(rt)
	cmd.SetArgs([]string{"ses-1"})
	cmd.SilenceUsage, cmd.SilenceErrors = true, true
	if err := cmd.Execute(); err == nil {
		t.Fatal("expected an error when --email is missing")
	}
	if called {
		t.Error("must not call the API without an email")
	}
}

func TestShareListPassesPortFilter(t *testing.T) {
	var gotQuery string
	rt, _ := shareRuntime(t, func(w http.ResponseWriter, r *http.Request) {
		gotQuery = r.URL.RawQuery
		_, _ = w.Write([]byte(`{"shares":[],"count":0}`))
	})

	cmd := newSessionShareList(rt)
	cmd.SetArgs([]string{"ses-1", "--port", "8080"})
	if err := cmd.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}
	if gotQuery != "port=8080" {
		t.Errorf("query = %q, want port=8080", gotQuery)
	}
}

func TestShareListOmitsPortWhenUnset(t *testing.T) {
	var gotQuery string
	rt, _ := shareRuntime(t, func(w http.ResponseWriter, r *http.Request) {
		gotQuery = r.URL.RawQuery
		_, _ = w.Write([]byte(`{"shares":[],"count":0}`))
	})

	cmd := newSessionShareList(rt)
	cmd.SetArgs([]string{"ses-1"})
	if err := cmd.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}
	if gotQuery != "" {
		t.Errorf("query = %q, want empty (all ports)", gotQuery)
	}
}

func TestShareRevokeDeletesTheShare(t *testing.T) {
	var gotPath, gotMethod string
	rt, _ := shareRuntime(t, func(w http.ResponseWriter, r *http.Request) {
		gotPath, gotMethod = r.URL.Path, r.Method
		_, _ = w.Write([]byte(`{"revoked":true}`))
	})

	cmd := newSessionShareRevoke(rt)
	cmd.SetArgs([]string{"ses-1", "share-9"})
	if err := cmd.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}
	if gotMethod != http.MethodDelete {
		t.Errorf("method = %q, want DELETE", gotMethod)
	}
	if !strings.HasSuffix(gotPath, "/sessions/ses-1/preview-shares/share-9") {
		t.Errorf("path = %q", gotPath)
	}
}

// --- previews -------------------------------------------------------------

const previewsPayload = `{
  "sessions": [
    {"id":"s1","name":"Bippy deck","state":"paused","preview_url":"https://3000-a.dev.runtm.com"},
    {"id":"s2","name":"No server yet","state":"running","preview_url":""},
    {"id":"s3","name":"Tour","state":"running","preview_url":"https://3000-c.dev.runtm.com"}
  ],
  "total": 3
}`

func TestPreviewsIsScopedToTheCallerByDefault(t *testing.T) {
	var gotQuery string
	rt, stdout := shareRuntime(t, func(w http.ResponseWriter, r *http.Request) {
		gotQuery = r.URL.RawQuery
		_, _ = w.Write([]byte(previewsPayload))
	})

	cmd := newSessionPreviews(rt)
	cmd.SetArgs([]string{})
	if err := cmd.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}

	// The bug this command exists to fix: asking for "my preview URLs" and
	// getting the whole org back because team_mode leaked in.
	if strings.Contains(gotQuery, "team_mode") {
		t.Errorf("must not request team mode by default, query = %q", gotQuery)
	}

	var out struct {
		Count int    `json:"count"`
		Scope string `json:"scope"`
	}
	if err := json.Unmarshal(stdout.Bytes(), &out); err != nil {
		t.Fatalf("unmarshal stdout: %v (%s)", err, stdout.String())
	}
	if out.Scope != "mine" {
		t.Errorf("scope = %q, want mine", out.Scope)
	}
}

func TestPreviewsDropsSessionsWithoutAUrl(t *testing.T) {
	rt, stdout := shareRuntime(t, func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(previewsPayload))
	})

	cmd := newSessionPreviews(rt)
	cmd.SetArgs([]string{})
	if err := cmd.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}

	var out struct {
		Previews []sessionPreviewRow `json:"previews"`
		Count    int                 `json:"count"`
	}
	if err := json.Unmarshal(stdout.Bytes(), &out); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if out.Count != 2 {
		t.Fatalf("count = %d, want 2 (s2 has no preview URL)", out.Count)
	}
	for _, p := range out.Previews {
		if p.ID == "s2" {
			t.Error("s2 has no preview URL and should be omitted")
		}
	}
	// A paused session still lists — opening the link wakes it.
	if out.Previews[0].State != "paused" || out.Previews[0].PreviewURL == "" {
		t.Errorf("paused session should still be listed: %+v", out.Previews[0])
	}
}

func TestPreviewsAllIncludesSessionsWithoutAUrl(t *testing.T) {
	rt, stdout := shareRuntime(t, func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(previewsPayload))
	})

	cmd := newSessionPreviews(rt)
	cmd.SetArgs([]string{"--all"})
	if err := cmd.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}

	var out struct {
		Count int `json:"count"`
	}
	_ = json.Unmarshal(stdout.Bytes(), &out)
	if out.Count != 3 {
		t.Errorf("count = %d, want 3 with --all", out.Count)
	}
}

func TestPreviewsTeamModeIsOptIn(t *testing.T) {
	var gotQuery string
	rt, stdout := shareRuntime(t, func(w http.ResponseWriter, r *http.Request) {
		gotQuery = r.URL.RawQuery
		_, _ = w.Write([]byte(previewsPayload))
	})

	cmd := newSessionPreviews(rt)
	cmd.SetArgs([]string{"--team-mode"})
	if err := cmd.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}
	if !strings.Contains(gotQuery, "team_mode=true") {
		t.Errorf("query = %q, want team_mode=true", gotQuery)
	}
	// Scope is reported so an agent can tell the user whose sessions these are.
	if !strings.Contains(stdout.String(), `"scope": "team"`) {
		t.Errorf("stdout should report team scope: %s", stdout.String())
	}
}

func TestPreviewsFallsBackToRawBodyOnUnknownShape(t *testing.T) {
	rt, stdout := shareRuntime(t, func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`["unexpected"]`))
	})

	cmd := newSessionPreviews(rt)
	cmd.SetArgs([]string{})
	if err := cmd.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}
	// Better to hand back data we can't project than to silently print zero.
	if !strings.Contains(stdout.String(), "unexpected") {
		t.Errorf("expected raw passthrough, got %s", stdout.String())
	}
}
