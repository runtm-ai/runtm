package cmd

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"
)

// Launching a session *as* a roster agent is how one session spawns a
// subagent, so both entry points have to put agent_id (and the approval that
// may gate it) on the wire — and must not confuse it with --agent, the
// coding harness.

func TestSessionCreateSendsAgentIDAndApproval(t *testing.T) {
	var gotPath string
	var gotBody map[string]any

	rt, _ := shareRuntime(t, func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		_ = json.NewDecoder(r.Body).Decode(&gotBody)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"id":"ses-1","state":"creating"}`))
	})

	cmd := newSessionCreate(rt)
	cmd.SetArgs([]string{
		"--agent-id", "b1c506b1-7c5a-498d-8c45-45a235dda51f",
		"--approval-id", "ap-1",
		"--agent", "codex",
	})
	if err := cmd.Execute(); err != nil {
		t.Fatalf("create: %v", err)
	}

	// The client normalizes the base to the Next.js /cloud proxy, which
	// forwards to the backend's /api/sessions/.
	if !strings.HasSuffix(gotPath, "/sessions/") {
		t.Errorf("path = %q, want it to end in /sessions/", gotPath)
	}
	if gotBody["agent_id"] != "b1c506b1-7c5a-498d-8c45-45a235dda51f" {
		t.Errorf("agent_id = %v", gotBody["agent_id"])
	}
	if gotBody["approval_id"] != "ap-1" {
		t.Errorf("approval_id = %v", gotBody["approval_id"])
	}
	// The harness is a separate field; --agent-id must not overwrite it.
	if gotBody["agent"] != "codex" {
		t.Errorf("agent = %v, want codex", gotBody["agent"])
	}
}

func TestSessionLaunchSendsAgentIDAndApproval(t *testing.T) {
	var gotPath string
	var gotBody map[string]any

	rt, _ := shareRuntime(t, func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		_ = json.NewDecoder(r.Body).Decode(&gotBody)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"id":"ses-2","state":"creating"}`))
	})

	cmd := newSessionLaunch(rt)
	cmd.SetArgs([]string{
		"--prompt", "reconcile yesterday's ledger",
		"--agent-id", "b277d18b-b2c8-4410-9e10-8c7b2a415d73",
		"--approval-id", "ap-2",
	})
	if err := cmd.Execute(); err != nil {
		t.Fatalf("launch: %v", err)
	}

	if !strings.HasSuffix(gotPath, "/v0/sessions/launch") {
		t.Errorf("path = %q, want it to end in /v0/sessions/launch", gotPath)
	}
	if gotBody["agent_id"] != "b277d18b-b2c8-4410-9e10-8c7b2a415d73" {
		t.Errorf("agent_id = %v", gotBody["agent_id"])
	}
	if gotBody["approval_id"] != "ap-2" {
		t.Errorf("approval_id = %v", gotBody["approval_id"])
	}
}

func TestAgentsListCanLaunchFilter(t *testing.T) {
	var gotQuery string
	rt, _ := shareRuntime(t, func(w http.ResponseWriter, r *http.Request) {
		gotQuery = r.URL.RawQuery
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`[]`))
	})
	t.Setenv("RUNTM_ORG_ID", "org-1")

	cmd := newAgentList(rt)
	cmd.SetArgs([]string{"--can-launch"})
	if err := cmd.Execute(); err != nil {
		t.Fatalf("list: %v", err)
	}
	if gotQuery != "can_launch=true" {
		t.Errorf("query = %q, want can_launch=true", gotQuery)
	}

	cmd = newAgentList(rt)
	cmd.SetArgs(nil)
	if err := cmd.Execute(); err != nil {
		t.Fatalf("list: %v", err)
	}
	if gotQuery != "" {
		t.Errorf("query = %q, want none when the flag is unset", gotQuery)
	}
}

func TestSessionCreateAndLaunchOmitAgentIDWhenUnset(t *testing.T) {
	var bodies []map[string]any
	rt, _ := shareRuntime(t, func(w http.ResponseWriter, r *http.Request) {
		var b map[string]any
		_ = json.NewDecoder(r.Body).Decode(&b)
		bodies = append(bodies, b)
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"id":"ses-3","state":"creating"}`))
	})

	create := newSessionCreate(rt)
	create.SetArgs(nil)
	if err := create.Execute(); err != nil {
		t.Fatalf("create: %v", err)
	}
	launch := newSessionLaunch(rt)
	launch.SetArgs([]string{"--prompt", "hello"})
	if err := launch.Execute(); err != nil {
		t.Fatalf("launch: %v", err)
	}

	for i, b := range bodies {
		if _, ok := b["agent_id"]; ok {
			t.Errorf("body %d sent agent_id when the flag was unset", i)
		}
		if _, ok := b["approval_id"]; ok {
			t.Errorf("body %d sent approval_id when the flag was unset", i)
		}
	}
}
