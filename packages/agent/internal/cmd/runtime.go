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

// requireOrgClient resolves the API client and ensures org context is set.
// Use for routes that require X-Organization-Id (team telemetry, org
// instructions, guardrails, team secrets, etc.). Writes a friendly silent
// error to stdout when org is missing so the agent surfaces it cleanly.
func requireOrgClient(rt *Runtime, what string) (*client.Client, *auth.Credentials, error) {
	c, creds, err := rt.Client()
	if err != nil {
		return nil, nil, err
	}
	if creds.OrganizationID == "" {
		rt.WriteObject(map[string]any{
			"error": what + " is org-scoped. Pass --org <id> or set RUNTM_ORG_ID.",
			"hint":  "Switch context in the dashboard and copy the org ID, or run `runtm auth status` to inspect the active org.",
		})
		return nil, nil, errSilent
	}
	return c, creds, nil
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
		out, mErr := json.MarshalIndent(v, "", "  ")
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
	out, err := json.MarshalIndent(v, "", "  ")
	if err != nil {
		fmt.Fprintf(r.Stderr, `{"error":"failed to marshal: %s"}`+"\n", err.Error())
		return
	}
	r.Stdout.Write(out)
	fmt.Fprintln(r.Stdout)
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
			payload["hint"] = "Run `runtm auth status` to inspect scopes and org context."
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
