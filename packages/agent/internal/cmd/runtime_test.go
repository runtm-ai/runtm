package cmd

import (
	"bytes"
	"fmt"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"

	"github.com/runtm-ai/runtm/packages/agent/internal/auth"
	"github.com/runtm-ai/runtm/packages/agent/internal/client"
)

// newTestRuntime returns a Runtime wired to discard stdout/stderr. Useful for
// helpers that don't care about printed output.
func newTestRuntime() *Runtime {
	return &Runtime{
		Flags:  &GlobalFlags{},
		Stdout: &bytes.Buffer{},
		Stderr: &bytes.Buffer{},
	}
}

// newTestClient builds a real client.Client pointed at the supplied test
// server. The credentials carry no org so we can exercise the bootstrap path.
func newTestClient(serverURL string) *client.Client {
	return client.New(&auth.Credentials{
		APIKey: "runtm_sk_test",
		APIURL: serverURL,
	})
}

func TestResolveKeyOrgIDReturnsOrgFromVerify(t *testing.T) {
	var calls int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&calls, 1)
		if r.URL.Path != "/auth/verify" {
			t.Errorf("unexpected path %q", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"organization_id":"org_abc"}`)
	}))
	defer srv.Close()

	rt := newTestRuntime()
	c := newTestClient(srv.URL)

	got, err := rt.resolveKeyOrgID(c)
	if err != nil {
		t.Fatalf("resolveKeyOrgID error: %v", err)
	}
	if got != "org_abc" {
		t.Errorf("orgID = %q, want org_abc", got)
	}

	// Second call must hit the cache, not the network.
	if _, err := rt.resolveKeyOrgID(c); err != nil {
		t.Fatalf("second call error: %v", err)
	}
	if calls != 1 {
		t.Errorf("verify hit %d times, want 1 (memoized)", calls)
	}
}

func TestResolveKeyOrgIDReturnsEmptyForPersonalKey(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		// Personal keys come back with organization_id missing/null.
		fmt.Fprint(w, `{"organization_id":null,"tenant_id":"user_xyz"}`)
	}))
	defer srv.Close()

	rt := newTestRuntime()
	c := newTestClient(srv.URL)

	got, err := rt.resolveKeyOrgID(c)
	if err != nil {
		t.Fatalf("resolveKeyOrgID error: %v", err)
	}
	if got != "" {
		t.Errorf("orgID = %q, want empty (personal key)", got)
	}
}

func TestResolveKeyOrgIDPropagatesVerifyError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
		fmt.Fprint(w, `{"detail":"Invalid API key"}`)
	}))
	defer srv.Close()

	rt := newTestRuntime()
	c := newTestClient(srv.URL)

	got, err := rt.resolveKeyOrgID(c)
	if err == nil {
		t.Fatalf("expected error, got nil (orgID=%q)", got)
	}
	if got != "" {
		t.Errorf("orgID on error = %q, want empty", got)
	}
}
