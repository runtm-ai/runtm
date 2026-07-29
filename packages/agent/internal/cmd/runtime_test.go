package cmd

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
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

// identityServer serves the supplied JSON at /v1/me and 404s everything else,
// mirroring the real /api/cloud proxy — which only forwards to /api/*, so a
// root-mounted route like /auth/verify is unreachable.
//
// The leading /cloud is stripped the way the proxy does, so this stub works
// both for clients built directly and for those whose base URL went through
// auth.Load (which appends the /cloud suffix).
func identityServer(t *testing.T, payload string, calls *int32) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if calls != nil {
			atomic.AddInt32(calls, 1)
		}
		if strings.TrimPrefix(r.URL.Path, "/cloud") != "/v1/me" {
			w.WriteHeader(http.StatusNotFound)
			fmt.Fprint(w, `{"detail":"Not Found"}`)
			return
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, payload)
	}))
}

func TestResolveKeyOrgIDReturnsOrgFromMe(t *testing.T) {
	var calls int32
	srv := identityServer(t, `{"organization_id":"org_abc","tenant_id":"org_abc","principal_id":"user_xyz"}`, &calls)
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
		t.Errorf("identity endpoint hit %d times, want 1 (memoized)", calls)
	}
}

// Regression guard: the bootstrap used to call /auth/verify, which the cloud
// proxy 404s, so every org-scoped command wrongly reported "no org".
func TestResolveKeyOrgIDUsesProxyReachableEndpoint(t *testing.T) {
	var gotPath string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"organization_id":"org_abc"}`)
	}))
	defer srv.Close()

	if _, err := newTestRuntime().resolveKeyOrgID(newTestClient(srv.URL)); err != nil {
		t.Fatalf("resolveKeyOrgID error: %v", err)
	}
	if gotPath != "/v1/me" {
		t.Errorf("requested %q, want /v1/me", gotPath)
	}
}

// Backends that predate organization_id on /v1/me only expose the legacy
// tenant_id, which equals the org for org-scoped keys.
func TestResolveKeyOrgIDFallsBackToTenantID(t *testing.T) {
	srv := identityServer(t, `{"tenant_id":"org_abc","principal_id":"user_xyz"}`, nil)
	defer srv.Close()

	got, err := rtResolve(t, srv.URL)
	if err != nil {
		t.Fatalf("resolveKeyOrgID error: %v", err)
	}
	if got != "org_abc" {
		t.Errorf("orgID = %q, want org_abc", got)
	}
}

func TestResolveKeyOrgIDReturnsEmptyForPersonalKey(t *testing.T) {
	// Personal keys carry a null organization_id and a tenant_id that is just
	// the user_id — it must not be mistaken for an org.
	srv := identityServer(t, `{"organization_id":null,"tenant_id":"user_xyz","principal_id":"user_xyz"}`, nil)
	defer srv.Close()

	got, err := rtResolve(t, srv.URL)
	if err != nil {
		t.Fatalf("resolveKeyOrgID error: %v", err)
	}
	if got != "" {
		t.Errorf("orgID = %q, want empty (personal key)", got)
	}
}

func TestResolveKeyOrgIDPropagatesLookupError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
		fmt.Fprint(w, `{"detail":"Invalid API key"}`)
	}))
	defer srv.Close()

	got, err := rtResolve(t, srv.URL)
	if err == nil {
		t.Fatalf("expected error, got nil (orgID=%q)", got)
	}
	if got != "" {
		t.Errorf("orgID on error = %q, want empty", got)
	}
}

func TestOrgFromIdentity(t *testing.T) {
	cases := []struct {
		name                         string
		orgID, tenantID, principalID string
		want                         string
	}{
		{"explicit org wins", "org_abc", "user_xyz", "user_xyz", "org_abc"},
		{"tenant differs from principal", "", "org_abc", "user_xyz", "org_abc"},
		{"tenant equals principal", "", "user_xyz", "user_xyz", ""},
		{"all empty", "", "", "", ""},
		{"tenant only, no principal", "", "org_abc", "", "org_abc"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := orgFromIdentity(tc.orgID, tc.tenantID, tc.principalID); got != tc.want {
				t.Errorf("orgFromIdentity(%q, %q, %q) = %q, want %q",
					tc.orgID, tc.tenantID, tc.principalID, got, tc.want)
			}
		})
	}
}

func rtResolve(t *testing.T, serverURL string) (string, error) {
	t.Helper()
	return newTestRuntime().resolveKeyOrgID(newTestClient(serverURL))
}

// orgRuntime wires a Runtime to a stub identity server the way a real
// invocation would: key from the env, base URL from the --api-url flag. The
// returned counter lets callers assert the identity endpoint was actually
// reached, so a routing regression can't masquerade as a personal key.
func orgRuntime(t *testing.T, payload string) (*Runtime, *bytes.Buffer, *int32) {
	t.Helper()
	var calls int32
	srv := identityServer(t, payload, &calls)
	t.Cleanup(srv.Close)
	t.Setenv("RUNTM_API_KEY", "runtm_sk_test")
	t.Setenv("RUNTM_ORG_ID", "")

	stdout := &bytes.Buffer{}
	return &Runtime{
		Flags:  &GlobalFlags{APIURL: srv.URL},
		Stdout: stdout,
		Stderr: &bytes.Buffer{},
	}, stdout, &calls
}

func TestRequireOrgClientAdoptsOrgFromKey(t *testing.T) {
	rt, _, _ := orgRuntime(t, `{"organization_id":"org_abc","tenant_id":"org_abc","principal_id":"user_xyz"}`)

	_, creds, err := requireOrgClient(rt, "org templates")
	if err != nil {
		t.Fatalf("requireOrgClient error: %v", err)
	}
	if creds.OrganizationID != "org_abc" {
		t.Errorf("OrganizationID = %q, want org_abc", creds.OrganizationID)
	}
}

// A personal key genuinely cannot reach an org, so the guidance must point at
// creating an org-scoped key rather than at --org / RUNTM_ORG_ID, which the API
// rejects with 403.
func TestRequireOrgClientPersonalKeyGuidance(t *testing.T) {
	rt, stdout, calls := orgRuntime(t, `{"organization_id":null,"tenant_id":"user_xyz","principal_id":"user_xyz"}`)

	if _, _, err := requireOrgClient(rt, "org templates"); err != errSilent {
		t.Fatalf("err = %v, want errSilent", err)
	}
	if *calls != 1 {
		t.Fatalf("identity endpoint hit %d times, want 1 — the lookup must succeed "+
			"and report a personal key, not fail to route", *calls)
	}

	var payload struct {
		Error string `json:"error"`
		Hint  string `json:"hint"`
	}
	if err := json.Unmarshal(stdout.Bytes(), &payload); err != nil {
		t.Fatalf("unmarshal output %q: %v", stdout.String(), err)
	}
	if !strings.Contains(payload.Error, "org-scoped key") {
		t.Errorf("error should point at creating an org-scoped key, got %q", payload.Error)
	}
	// Guard against regressing to the old advice, which does not work.
	for _, banned := range []string{"Pass --org", "set RUNTM_ORG_ID"} {
		if strings.Contains(payload.Error, banned) {
			t.Errorf("error recommends %q, which the API rejects with 403: %q", banned, payload.Error)
		}
	}
	if !strings.Contains(payload.Hint, "403") {
		t.Errorf("hint should explain the 403, got %q", payload.Hint)
	}
}

func TestErrorHintsNameThisBinary(t *testing.T) {
	// A hint that names a command of this binary has to spell it `runtm-api`.
	// `runtm` is the separate pip CLI, so `runtm auth status` sent people to a
	// tool that does not have the subcommand. Only `runtm login` is genuinely
	// the pip CLI's, so that one stays.
	for status, hint := range map[int]string{
		http.StatusUnauthorized:    "Check RUNTM_API_KEY",
		http.StatusForbidden:       "auth status",
		http.StatusNotFound:        "list command",
		http.StatusTooManyRequests: "Rate limited",
	} {
		stderr := &bytes.Buffer{}
		rt := &Runtime{Flags: &GlobalFlags{}, Stdout: &bytes.Buffer{}, Stderr: stderr}

		rt.ReportError(&client.APIError{Status: status, Detail: "nope"})

		var payload struct {
			Hint string `json:"hint"`
		}
		if err := json.Unmarshal(stderr.Bytes(), &payload); err != nil {
			t.Fatalf("status %d: unmarshal %q: %v", status, stderr.String(), err)
		}
		if !strings.Contains(payload.Hint, hint) {
			t.Errorf("status %d: hint %q should mention %q", status, payload.Hint, hint)
		}
		if strings.Contains(payload.Hint, "`runtm ") && !strings.Contains(payload.Hint, "`runtm login") {
			t.Errorf("status %d: hint names the pip CLI for a runtm-api command: %q", status, payload.Hint)
		}
	}
}
