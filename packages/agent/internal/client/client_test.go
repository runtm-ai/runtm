package client

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/runtm-ai/runtm/packages/agent/internal/auth"
)

func newTestClient(srv *httptest.Server, orgID string) *Client {
	return New(&auth.Credentials{
		APIKey:         "runtm_sk_test",
		APIURL:         srv.URL,
		OrganizationID: orgID,
	})
}

func TestGetSendsBearerAndOrg(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("Authorization"); got != "Bearer runtm_sk_test" {
			t.Errorf("Authorization = %q", got)
		}
		if got := r.Header.Get("X-Organization-Id"); got != "org_abc" {
			t.Errorf("X-Organization-Id = %q", got)
		}
		w.WriteHeader(http.StatusOK)
		fmt.Fprint(w, `{"ok":true}`)
	}))
	defer srv.Close()

	c := newTestClient(srv, "org_abc")
	body, err := c.Get("/v0/sessions", nil)
	if err != nil {
		t.Fatalf("Get failed: %v", err)
	}
	if string(body) != `{"ok":true}` {
		t.Errorf("body = %q", body)
	}
}

func TestAPIErrorIncludesDetail(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
		fmt.Fprint(w, `{"detail":"Invalid API key"}`)
	}))
	defer srv.Close()

	c := newTestClient(srv, "")
	_, err := c.Get("/v1/me", nil)
	if err == nil {
		t.Fatal("expected error")
	}
	apiErr, ok := err.(*APIError)
	if !ok {
		t.Fatalf("err type = %T, want *APIError", err)
	}
	if apiErr.Status != http.StatusUnauthorized {
		t.Errorf("Status = %d, want 401", apiErr.Status)
	}
	if apiErr.Detail != "Invalid API key" {
		t.Errorf("Detail = %q", apiErr.Detail)
	}
	if !IsAuthError(err) {
		t.Errorf("IsAuthError returned false")
	}
}

func TestAPIErrorWithNestedDetail(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusForbidden)
		fmt.Fprint(w, `{"detail":{"error":"Missing scope sessions:write"}}`)
	}))
	defer srv.Close()

	c := newTestClient(srv, "")
	_, err := c.Get("/v0/sessions", nil)
	apiErr, ok := err.(*APIError)
	if !ok {
		t.Fatalf("err type = %T, want *APIError", err)
	}
	if apiErr.Detail != "Missing scope sessions:write" {
		t.Errorf("Detail = %q, want nested error", apiErr.Detail)
	}
}

func TestPostJSONSendsBody(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("Content-Type"); got != "application/json" {
			t.Errorf("Content-Type = %q", got)
		}
		var payload map[string]any
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Fatalf("decode: %v", err)
		}
		if payload["prompt"] != "hello" {
			t.Errorf("prompt = %v", payload["prompt"])
		}
		w.WriteHeader(http.StatusCreated)
		fmt.Fprint(w, `{"id":"ses_1"}`)
	}))
	defer srv.Close()

	c := newTestClient(srv, "")
	body, err := c.PostJSON("/v0/sessions/launch", map[string]any{"prompt": "hello"})
	if err != nil {
		t.Fatalf("PostJSON failed: %v", err)
	}
	if !strings.Contains(string(body), "ses_1") {
		t.Errorf("body = %q", body)
	}
}

func TestStreamSSEParsesEvents(t *testing.T) {
	sseBody := `event: assistant_message
data: {"type":"assistant_message","content":"hi"}

event: result
data: {"type":"result","cost_usd":0.01}

event: done
data: {"type":"done"}

`
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("Accept"); got != "text/event-stream" {
			t.Errorf("Accept = %q", got)
		}
		w.Header().Set("Content-Type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		io.Copy(w, strings.NewReader(sseBody))
	}))
	defer srv.Close()

	c := newTestClient(srv, "")
	var out bytes.Buffer
	if err := c.StreamSSE("/v0/sessions/ses_1/prompt", map[string]any{"prompt": "x"}, &out); err != nil {
		t.Fatalf("StreamSSE failed: %v", err)
	}

	lines := strings.Split(strings.TrimSpace(out.String()), "\n")
	if len(lines) != 3 {
		t.Fatalf("got %d lines, want 3:\n%s", len(lines), out.String())
	}

	var first map[string]any
	if err := json.Unmarshal([]byte(lines[0]), &first); err != nil {
		t.Fatalf("line 0 not JSON: %v", err)
	}
	if first["event"] != "assistant_message" {
		t.Errorf("first event = %v, want assistant_message", first["event"])
	}

	var last map[string]any
	if err := json.Unmarshal([]byte(lines[2]), &last); err != nil {
		t.Fatalf("line 2 not JSON: %v", err)
	}
	if last["event"] != "done" {
		t.Errorf("last event = %v, want done", last["event"])
	}
}
