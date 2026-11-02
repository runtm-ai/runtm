package cmd

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestAgentListHasAllFlag(t *testing.T) {
	list := findSub(t, NewAgentsCommand(newTestRuntime()), "list")
	if list.Flags().Lookup("all") == nil {
		t.Fatal("agents list should have an --all flag")
	}
}

func TestListAllAgentsKeysEachSourceAndReportsErrors(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch strings.TrimPrefix(r.URL.Path, "/cloud") {
		case "/v1/agents":
			fmt.Fprint(w, `[{"id":"a1","name":"Pepper"}]`)
		case "/v1/slack/integrations":
			fmt.Fprint(w, `[{"id":"s1","name":"Runtime","config":{"default_model":"sonnet"}}]`)
		case "/v1/email/integrations":
			w.WriteHeader(http.StatusForbidden)
			fmt.Fprint(w, `{"detail":"Missing required scope: integrations:read"}`)
		default:
			fmt.Fprint(w, `[]`)
		}
	}))
	defer srv.Close()

	raw, err := json.Marshal(listAllAgents(newTestClient(srv.URL)))
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	var got map[string]any
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	for _, key := range []string{"roster", "slack", "github", "linear"} {
		if _, ok := got[key]; !ok {
			t.Errorf("missing %q in %s", key, raw)
		}
	}
	if _, ok := got["email"]; ok {
		t.Errorf("email failed and should not be listed: %s", raw)
	}
	errs, _ := got["errors"].(map[string]any)
	email, _ := errs["email"].(map[string]any)
	if email["status"] != float64(403) {
		t.Errorf("errors.email = %v, want status 403", errs["email"])
	}
	if !strings.Contains(string(raw), `"default_model":"sonnet"`) {
		t.Errorf("slack config should pass through: %s", raw)
	}
}

func TestAgentsUpdateAccessGrant(t *testing.T) {
	var method, path string
	var body map[string]any
	rt, _ := shareRuntime(t, func(w http.ResponseWriter, r *http.Request) {
		method, path = r.Method, r.URL.Path
		_ = json.NewDecoder(r.Body).Decode(&body)
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{}`)
	})
	t.Setenv("RUNTM_ORG_ID", "org_test")
	cmd := NewAgentsCommand(rt)
	cmd.SetArgs([]string{"update", "ag-1", "--access-grant", `{"allowed_templates":["gtm-machine"]}`})
	if err := cmd.Execute(); err != nil {
		t.Fatalf("execute: %v", err)
	}

	if method != http.MethodPatch || !strings.HasSuffix(path, "/v1/agents/ag-1") {
		t.Errorf("%s %s, want PATCH of the roster agent", method, path)
	}
	grant, _ := body["access_grant"].(map[string]any)
	if fmt.Sprint(grant["allowed_templates"]) != "[gtm-machine]" {
		t.Errorf("body = %v, want the grant sent as given", body)
	}
}

func TestAccessGrantIsRosterOnly(t *testing.T) {
	rt, _ := shareRuntime(t, func(w http.ResponseWriter, r *http.Request) {
		t.Errorf("unexpected %s %s", r.Method, r.URL.Path)
	})
	t.Setenv("RUNTM_ORG_ID", "org_test")
	cmd := NewAgentsCommand(rt)
	cmd.SetArgs([]string{"update", "s-1", "--type", "slack", "--access-grant", `{}`})
	cmd.SilenceUsage, cmd.SilenceErrors = true, true
	if err := cmd.Execute(); err == nil || !strings.Contains(err.Error(), "roster") {
		t.Errorf("err = %v, want a roster-only error", err)
	}
}
