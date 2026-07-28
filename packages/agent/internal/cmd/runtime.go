// Package cmd wires Cobra subcommands. The shared helpers in this file build
// the API client lazily from root flags and provide consistent JSON output +
// exit-code semantics so AI agents can rely on predictable behavior.
package cmd

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"sync"

	"github.com/runtm-ai/runtm/packages/agent/internal/auth"
	"github.com/runtm-ai/runtm/packages/agent/internal/client"
)

// Exit codes are stable so agents can branch on them without parsing output.
const (
	ExitOK        = 0
	ExitAPIError  = 1
	ExitAuthError = 2
	ExitUsage     = 3
)

// GlobalFlags are populated by root command persistent flags.
type GlobalFlags struct {
	APIURL string
	Org    string
}

// Runtime wires flags + auth + client and is passed to every subcommand.
type Runtime struct {
	Flags  *GlobalFlags
	Stdout io.Writer
	Stderr io.Writer

	// keyOrgOnce + keyOrgID memoize the org ID embedded in the API key, so
	// org-scoped commands like `runtm-api template list` work without the
	// user having to set RUNTM_ORG_ID for org keys (the backend knows the
	// org from the key, we just have to ask it once).
	keyOrgOnce sync.Once
	keyOrgID   string
	keyOrgErr  error
}

// NewRuntime returns a Runtime that writes to os.Stdout / os.Stderr.
func NewRuntime(flags *GlobalFlags) *Runtime {
	return &Runtime{Flags: flags, Stdout: os.Stdout, Stderr: os.Stderr}
}

// Client resolves credentials and returns a configured API client. Returns an
// error with ExitAuthError-style messaging if credentials are missing.
func (r *Runtime) Client() (*client.Client, *auth.Credentials, error) {
	creds, err := auth.Load(r.Flags.APIURL, r.Flags.Org)
	if err != nil {
		return nil, nil, err
	}
	return client.New(creds), creds, nil
}

// resolveKeyOrgID asks the backend (once) which org this API key is bound to.
// Returns "" when the key is personal or when the lookup fails (in which case
// the caller's subsequent request will surface the real error). Memoized so a
// single CLI invocation pays at most one extra round-trip.
//
// This reads /v1/me, not /auth/verify. The backend mounts /auth/verify at its
// root, while the CLI speaks to the /api/cloud proxy which only forwards to
// /api/* -- so /auth/verify is a 404 over this transport.
func (r *Runtime) resolveKeyOrgID(c *client.Client) (string, error) {
	r.keyOrgOnce.Do(func() {
		body, err := c.Get("/v1/me", nil)
		if err != nil {
			r.keyOrgErr = err
			return
		}
		var v struct {
			OrganizationID string `json:"organization_id"`
			TenantID       string `json:"tenant_id"`
			PrincipalID    string `json:"principal_id"`
		}
		if err := json.Unmarshal(body, &v); err != nil {
			r.keyOrgErr = err
			return
		}
		r.keyOrgID = orgFromIdentity(v.OrganizationID, v.TenantID, v.PrincipalID)
	})
	return r.keyOrgID, r.keyOrgErr
}

// orgFromIdentity extracts the org binding from a /v1/me payload.
// organization_id is authoritative when the backend supplies it. Older
// backends only return the legacy tenant_id, which holds the org for org keys
// but the user_id for personal ones -- so it only counts as an org when it
// differs from the principal.
func orgFromIdentity(orgID, tenantID, principalID string) string {
	if orgID != "" {
		return orgID
	}
	if tenantID != "" && tenantID != principalID {
		return tenantID
	}
	return ""
}

// requireOrgClient resolves the API client and ensures an org context is
// available for routes that require X-Organization-Id (team telemetry, org
// instructions, guardrails, team secrets, templates, etc.).
//
// The org is a property of the API key, not a caller-supplied parameter: the
// backend 403s a personal key that sends X-Organization-Id, and 403s an org key
// whose header names a different org. So --org / RUNTM_ORG_ID can only restate
// the key's own binding -- it can never grant access to an org the key lacks.
//
// Resolution order:
//  1. --org flag / RUNTM_ORG_ID env var (already populated on creds).
//  2. The org embedded in the API key, fetched once via /v1/me.
//  3. Surface a friendly silent error pointing the agent at the only real fix.
func requireOrgClient(rt *Runtime, what string) (*client.Client, *auth.Credentials, error) {
	c, creds, err := rt.Client()
	if err != nil {
		return nil, nil, err
	}
	if creds.OrganizationID != "" {
		return c, creds, nil
	}

	// Try to bootstrap the org from the key itself.
	orgID, verifyErr := rt.resolveKeyOrgID(c)
	if verifyErr == nil && orgID != "" {
		creds.OrganizationID = orgID
		// Rebuild the client so the X-Organization-Id header is attached
		// to every subsequent request in this command.
		return client.New(creds), creds, nil
	}

	rt.WriteObject(map[string]any{
		"error": what + " is org-scoped, but this API key is personal (it has no org). Create an org-scoped key at https://app.runtm.com > Settings > API Keys and use it instead.",
		"hint":  "The org is fixed when the key is created; --org / RUNTM_ORG_ID cannot grant access to one (the API rejects a personal key that sends an org with 403). Run `runtm-api auth status` to inspect the active key.",
	})
	return nil, nil, errSilent
}

// WriteJSON writes raw API response bytes (already JSON) to stdout with a
// trailing newline so the output is line-friendly. If the bytes are not valid
// JSON it falls back to writing them verbatim.
func (r *Runtime) WriteJSON(raw []byte) {
	if len(raw) == 0 {
		fmt.Fprintln(r.Stdout, "{}")
		return
	}
	// Pretty-print if it parses cleanly.
	var v any
	if err := json.Unmarshal(raw, &v); err == nil {
		out, mErr := marshalIndentNoEscape(v)
		if mErr == nil {
			r.Stdout.Write(out)
			fmt.Fprintln(r.Stdout)
			return
		}
	}
	r.Stdout.Write(bytes.TrimRight(raw, "\n"))
	fmt.Fprintln(r.Stdout)
}

// WriteObject marshals an arbitrary value as JSON and writes it.
func (r *Runtime) WriteObject(v any) {
	out, err := marshalIndentNoEscape(v)
	if err != nil {
		fmt.Fprintf(r.Stderr, `{"error":"failed to marshal: %s"}`+"\n", err.Error())
		return
	}
	r.Stdout.Write(out)
	fmt.Fprintln(r.Stdout)
}

// marshalIndentNoEscape is json.MarshalIndent without HTML escaping, so URLs
// in output keep literal & < > instead of & < > (e.g. the &
// query separators in a Slack authorize_url).
func marshalIndentNoEscape(v any) ([]byte, error) {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	enc.SetIndent("", "  ")
	if err := enc.Encode(v); err != nil {
		return nil, err
	}
	// Encoder.Encode appends a trailing newline; callers add their own.
	return bytes.TrimRight(buf.Bytes(), "\n"), nil
}

// ReportError prints a structured JSON error to stderr and returns the exit
// code the CLI should terminate with.
func (r *Runtime) ReportError(err error) int {
	if err == nil {
		return ExitOK
	}
	if errors.Is(err, auth.ErrNoCredentials) {
		r.writeErrorJSON(map[string]any{
			"error": err.Error(),
			"hint":  "Set RUNTM_API_KEY=runtm_sk_... or run `runtm login` (pip CLI).",
		})
		return ExitAuthError
	}

	var apiErr *client.APIError
	if errors.As(err, &apiErr) {
		payload := map[string]any{
			"error":  apiErr.Detail,
			"status": apiErr.Status,
		}
		if apiErr.Detail == "" {
			payload["error"] = http.StatusText(apiErr.Status)
		}
		switch apiErr.Status {
		case http.StatusUnauthorized:
			payload["hint"] = "Check RUNTM_API_KEY or rotate the key in the dashboard."
		case http.StatusForbidden:
			payload["hint"] = "Run `runtm-api auth status` to inspect scopes and org context."
		case http.StatusNotFound:
			payload["hint"] = "Run the matching list command to discover valid IDs."
		case http.StatusTooManyRequests:
			payload["hint"] = "Rate limited. Back off and retry."
		}
		r.writeErrorJSON(payload)
		if client.IsAuthError(err) {
			return ExitAuthError
		}
		return ExitAPIError
	}

	r.writeErrorJSON(map[string]any{"error": err.Error()})
	return ExitAPIError
}

func (r *Runtime) writeErrorJSON(payload map[string]any) {
	out, err := json.MarshalIndent(payload, "", "  ")
	if err != nil {
		fmt.Fprintf(r.Stderr, `{"error":%q}`+"\n", payload["error"])
		return
	}
	r.Stderr.Write(out)
	fmt.Fprintln(r.Stderr)
}
