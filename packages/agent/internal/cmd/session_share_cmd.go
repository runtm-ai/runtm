// Package cmd: preview sharing and preview-URL discovery.
//
// A preview share grants ONE email address access to ONE port of a session's
// live preview. It is deliberately the narrowest grant in the product: the
// invitee gets the preview and nothing else — no workspace, no terminal, no
// prompting — and they are not added to the organization. They do need a
// Runtm account, but not before being invited; the grant binds to their user
// id the first time they sign in and open the link.
//
// This exists because the share API is dual-mode (Bearer API key or the web
// app's HMAC), but until now only the web app could reach it. Anyone driving
// Runtm from the CLI had to open the dashboard to hand a prototype to someone
// outside their org.
//
// Wired into `runtm session` from root.go via `AddSessionShare(sessionCmd, rt)`.
package cmd

import (
	"encoding/json"
	"fmt"
	"net/url"
	"strconv"

	"github.com/spf13/cobra"
)

// AddSessionShare attaches the preview-share tree and the `previews` listing
// to the existing `runtm session` command.
func AddSessionShare(sessionCmd *cobra.Command, rt *Runtime) {
	share := &cobra.Command{
		Use:   "share",
		Short: "Share a session's live preview with someone outside the org",
		Long: `Grant one email address access to one port of a session's live preview.

The invitee can open that preview and nothing else — no workspace, no
terminal, no prompting — and they are not added to your organization. They
need a Runtm account to open it, but not before you invite them.

Paused sandboxes wake up automatically when a shared link is opened, so a
share keeps working after the ~20-minute idle pause.

  runtm-api session share create <session_id> --email dev@acme.com --port 3000
  runtm-api session share list   <session_id>
  runtm-api session share revoke <session_id> <share_id>`,
	}
	share.AddCommand(
		newSessionShareCreate(rt),
		newSessionShareList(rt),
		newSessionShareRevoke(rt),
	)
	sessionCmd.AddCommand(share, newSessionPreviews(rt))
}

func newSessionShareCreate(rt *Runtime) *cobra.Command {
	var (
		email      string
		port       int
		previewURL string
	)
	cmd := &cobra.Command{
		Use:   "create <session_id>",
		Short: "Invite an email to one port of a preview (POST /api/sessions/{id}/preview-shares)",
		Long: `Grants <email> access to <port> of this session's preview and emails them
the link. Re-inviting an address that already has the port is a no-op, and no
second email is sent.

The response includes "preview_url" — the link to send if "emailed" is false
(which happens when email delivery is not configured).

Required scope: sessions:write`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			if email == "" {
				return fmt.Errorf("--email is required")
			}
			body := map[string]any{"email": email, "port": port}
			// Only sent when the caller already knows the live URL (the web
			// app does, including any path). The server derives one otherwise.
			if previewURL != "" {
				body["preview_url"] = previewURL
			}
			resp, err := c.PostJSON("/sessions/"+url.PathEscape(args[0])+"/preview-shares", body)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&email, "email", "", "Email address to invite (required)")
	cmd.Flags().IntVar(&port, "port", 3000, "Preview port to share")
	cmd.Flags().StringVar(&previewURL, "preview-url", "", "Override the emailed link (defaults to the session's preview URL for this port)")
	return cmd
}

func newSessionShareList(rt *Runtime) *cobra.Command {
	var port int
	cmd := &cobra.Command{
		Use:   "list <session_id>",
		Short: "List preview shares (GET /api/sessions/{id}/preview-shares)",
		Long: `Lists who has access to this session's preview. "has_accessed" tells you
whether the invitee ever actually opened it.

Required scope: sessions:read`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			q := url.Values{}
			if port > 0 {
				q.Set("port", strconv.Itoa(port))
			}
			resp, err := c.Get("/sessions/"+url.PathEscape(args[0])+"/preview-shares", q)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().IntVar(&port, "port", 0, "Only shares for this port (default: all ports)")
	return cmd
}

func newSessionShareRevoke(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "revoke <session_id> <share_id>",
		Short: "Revoke a preview share (DELETE /api/sessions/{id}/preview-shares/{share_id})",
		Long: `Revokes access. Takes effect once the holder's current preview cookie
expires (a few minutes), not instantly.

Get <share_id> from 'runtm-api session share list <session_id>'.

Required scope: sessions:write`,
		Args: cobra.ExactArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			resp, err := c.Delete(
				"/sessions/" + url.PathEscape(args[0]) + "/preview-shares/" + url.PathEscape(args[1]),
			)
			return runJSON(rt, resp, err)
		},
	}
}

// --- previews (projection over /api/sessions/) ----------------------------

// sessionPreviewRow is the trimmed shape `previews` emits. `session list`
// returns the full session objects, which bury the preview URL in a wall of
// fields; agents asked for "my preview URLs" should get exactly that.
type sessionPreviewRow struct {
	ID         string `json:"id"`
	Name       string `json:"name,omitempty"`
	State      string `json:"state"`
	PreviewURL string `json:"preview_url"`
}

func newSessionPreviews(rt *Runtime) *cobra.Command {
	var (
		limit    int
		teamMode bool
		all      bool
	)
	cmd := &cobra.Command{
		Use:   "previews",
		Short: "List preview URLs for your own sessions",
		Long: `Lists the preview URLs of sessions YOU created, newest first.

Scoped to your API key's user by default. This is the command to reach for
when you want "my preview URLs" — 'session list --team-mode' in a busy org
returns every teammate's sessions too, which is rarely what was meant.

Sessions with no preview URL yet are omitted unless --all is passed. A paused
session still lists its URL: opening it wakes the sandbox automatically.

Required scope: sessions:read`,
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}

			q := url.Values{}
			q.Set("limit", strconv.Itoa(limit))
			if teamMode {
				q.Set("team_mode", "true")
			}
			resp, err := c.Get("/sessions/", q)
			if err != nil {
				return err
			}

			var payload struct {
				Sessions []struct {
					ID         string `json:"id"`
					Name       string `json:"name"`
					State      string `json:"state"`
					PreviewURL string `json:"preview_url"`
				} `json:"sessions"`
				Total int `json:"total"`
			}
			if err := json.Unmarshal(resp, &payload); err != nil {
				// Shape changed underneath us — hand back the raw body rather
				// than swallowing the data the caller asked for.
				rt.WriteJSON(resp)
				return nil
			}

			rows := make([]sessionPreviewRow, 0, len(payload.Sessions))
			for _, s := range payload.Sessions {
				if s.PreviewURL == "" && !all {
					continue
				}
				rows = append(rows, sessionPreviewRow{
					ID:         s.ID,
					Name:       s.Name,
					State:      s.State,
					PreviewURL: s.PreviewURL,
				})
			}

			rt.WriteObject(map[string]any{
				"previews": rows,
				"count":    len(rows),
				"scope":    previewScope(teamMode),
			})
			return nil
		},
	}
	cmd.Flags().IntVar(&limit, "limit", 50, "Number of sessions to scan (1-100)")
	cmd.Flags().BoolVar(&teamMode, "team-mode", false, "Include teammates' team-visible sessions instead of only your own")
	cmd.Flags().BoolVar(&all, "all", false, "Include sessions that have no preview URL yet")
	return cmd
}

func previewScope(teamMode bool) string {
	if teamMode {
		return "team"
	}
	return "mine"
}
