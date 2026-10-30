package cmd

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/spf13/cobra"

	"github.com/runtm-ai/runtm/packages/agent/internal/client"
)

// NewAssetsCommand returns `runtm-api assets`: the org's PDFs, images, videos
// and audio, kept in object storage instead of a git repo.
//
// Inside a sandbox the commands act on the sandbox's own session
// (RUNTM_SESSION_ID): agents upload private files and ask a person to publish
// them. Outside one they act on the org, for people with an org key.
//
// Routes:
//
//	POST   /api/v0/sessions/{id}/assets                      sessions:write
//	POST   /api/v0/sessions/{id}/assets/{asset_id}/complete  sessions:write
//	GET    /api/v0/sessions/{id}/assets[/{asset_id}]         sessions:read
//	POST   /api/v0/sessions/{id}/assets/publish              sessions:write (opens an approval)
//	POST   /api/assets[/{asset_id}/complete]                 assets:write
//	GET    /api/assets[/{asset_id}]                          assets:read
//	PATCH  /api/assets/{asset_id}                            assets:write
//	DELETE /api/assets/{asset_id}                            assets:write
func NewAssetsCommand(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:     "assets",
		Aliases: []string{"asset"},
		Short:   "Store PDFs, images, videos, and audio in object storage",
		Long: `Heavy files (decks and one-pagers as PDF, screenshots, videos) live in the
org's private object storage instead of a git repo. Every file has a link
that signed-in org members can open, and a person can switch it to "anyone
with the link" for prospects or a public website.

Inside a session (RUNTM_SESSION_ID is set) the commands act on that session:

  runtm-api assets upload ./out/kushki.pdf       # private; "url" is the member link
  runtm-api assets publish <asset_id> --note "For the Kushki follow-up"
                                                 # opens an approval, then end your turn
  runtm-api assets get <asset_id>                # "public_url" once a person approves

Outside a session they act on the org, with an org key holding assets:read
or assets:write:

  runtm-api assets upload ./deck.pdf
  runtm-api assets list --visibility link
  runtm-api assets publish <asset_id>            # public at once
  runtm-api assets unpublish <asset_id>          # the public link stops working
  runtm-api assets delete <asset_id> --yes

Types: pdf, png, jpg, jpeg, webp, gif, mp4, webm, mov, mp3, m4a, wav, up to
2 GB. HTML and SVG stay in the repo. Orgs without files enabled get 403
FEATURE_NOT_ENABLED.`,
	}
	cmd.AddCommand(
		newAssetsUpload(rt),
		newAssetsList(rt),
		newAssetsGet(rt),
		newAssetsPublish(rt),
		newAssetsUnpublish(rt),
		newAssetsDelete(rt),
	)
	return cmd
}

// assetRoutes is the route family a command uses: the session's when one is
// named (--session, else RUNTM_SESSION_ID), the org's otherwise.
type assetRoutes struct {
	c       *client.Client
	base    string
	session string
}

func (a *assetRoutes) item(id string) string {
	return a.base + "/" + url.PathEscape(id)
}

func resolveAssetRoutes(rt *Runtime, sessionFlag string, orgWide bool) (*assetRoutes, error) {
	session := strings.TrimSpace(sessionFlag)
	if session == "" && !orgWide {
		session = strings.TrimSpace(os.Getenv("RUNTM_SESSION_ID"))
	}
	if session != "" {
		c, _, err := rt.Client()
		if err != nil {
			return nil, err
		}
		return &assetRoutes{c: c, base: "/v0/sessions/" + url.PathEscape(session) + "/assets", session: session}, nil
	}
	c, _, err := requireOrgClient(rt, "assets")
	if err != nil {
		return nil, err
	}
	return &assetRoutes{c: c, base: "/assets"}, nil
}

const assetsSessionFlagHelp = "Session to act in (default: RUNTM_SESSION_ID; outside a session, the org)"

func newAssetsUpload(rt *Runtime) *cobra.Command {
	var session, name string
	cmd := &cobra.Command{
		Use:   "upload <file>",
		Short: "Upload a file, private to the org (POST .../assets, PUT, POST .../complete)",
		Long: `Uploads a local file to the org's object storage, streaming it from disk:

  1. POST /api/v0/sessions/{id}/assets (or /api/assets outside a session) with name, size, sha256
  2. HTTP PUT of the bytes to the signed URL it returns
  3. POST .../assets/{asset_id}/complete, which checks the size and marks the file ready

The file starts private: "url" in the output opens it for signed-in org
members. Run 'runtm-api assets publish' when it should be public.

  runtm-api assets upload ./out/kushki-one-pager.pdf
  runtm-api assets upload ./render.mp4 --name launch-video.mp4

Required scope: sessions:write in a session, assets:write outside one.`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			f, err := os.Open(args[0]) // #nosec G304 -- the named file is the command's argument
			if err != nil {
				return fmt.Errorf("open %s: %w", args[0], err)
			}
			defer f.Close()
			info, err := f.Stat()
			if err != nil {
				return err
			}
			if !info.Mode().IsRegular() {
				return fmt.Errorf("%s is not a regular file", args[0])
			}
			if info.Size() == 0 {
				return fmt.Errorf("%s is empty", args[0])
			}
			sum := sha256.New()
			if _, err := io.Copy(sum, f); err != nil {
				return fmt.Errorf("read %s: %w", args[0], err)
			}
			if name == "" {
				name = filepath.Base(args[0])
			}

			routes, err := resolveAssetRoutes(rt, session, false)
			if err != nil {
				return err
			}
			startResp, err := routes.c.PostJSON(routes.base, map[string]any{
				"filename":   name,
				"size_bytes": info.Size(),
				"sha256":     hex.EncodeToString(sum.Sum(nil)),
			})
			if err != nil {
				return runJSON(rt, startResp, err)
			}
			var started struct {
				Asset struct {
					ID string `json:"id"`
				} `json:"asset"`
				Upload struct {
					URL     string            `json:"url"`
					Headers map[string]string `json:"headers"`
				} `json:"upload"`
			}
			if err := json.Unmarshal(startResp, &started); err != nil {
				return fmt.Errorf("could not parse upload response: %w", err)
			}
			if started.Asset.ID == "" || started.Upload.URL == "" {
				return fmt.Errorf("upload response is missing the asset id or upload url")
			}
			if _, err := f.Seek(0, io.SeekStart); err != nil {
				return err
			}
			if err := putAsset(cmd.Context(), started.Upload.URL, started.Upload.Headers, f, info.Size()); err != nil {
				return err
			}
			done, err := routes.c.PostJSON(routes.item(started.Asset.ID)+"/complete", nil)
			return runJSON(rt, done, err)
		},
	}
	cmd.Flags().StringVar(&session, "session", "", assetsSessionFlagHelp)
	cmd.Flags().StringVar(&name, "name", "", "Name to store it under (default: the local file name; the extension sets the type)")
	return cmd
}

// assetsHTTPClient has no timeout: a large video can take a while, and
// Ctrl-C cancels through the command's context.
var assetsHTTPClient = &http.Client{}

// putAsset streams the bytes to the signed URL. The signature covers the
// content type and length, so both must match what the API was told.
func putAsset(ctx context.Context, signedURL string, headers map[string]string, body io.Reader, size int64) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodPut, signedURL, body)
	if err != nil {
		return err
	}
	req.ContentLength = size
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	resp, err := assetsHTTPClient.Do(req)
	if err != nil {
		return fmt.Errorf("upload to storage failed: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		detail, _ := io.ReadAll(io.LimitReader(resp.Body, 2048))
		return fmt.Errorf("upload to storage failed with status %d: %s", resp.StatusCode, strings.TrimSpace(string(detail)))
	}
	return nil
}

func newAssetsList(rt *Runtime) *cobra.Command {
	var (
		session    string
		all        bool
		visibility string
		limit      int
	)
	cmd := &cobra.Command{
		Use:   "list",
		Short: "List files, newest first (GET .../assets)",
		Long: `Lists this session's files inside a session, or the org's files outside one.

  runtm-api assets list
  runtm-api assets list --visibility link      # only files anyone with the link can open
  runtm-api assets list --all                  # the whole org, even inside a session

Required scope: sessions:read in a session, assets:read for the org.`,
		Args: cobra.NoArgs,
		RunE: func(cmd *cobra.Command, args []string) error {
			if all && session != "" {
				return fmt.Errorf("--all lists the whole org; drop --session")
			}
			if visibility != "" && visibility != "private" && visibility != "link" {
				return fmt.Errorf("--visibility must be private or link")
			}
			routes, err := resolveAssetRoutes(rt, session, all)
			if err != nil {
				return err
			}
			q := url.Values{}
			if visibility != "" {
				q.Set("visibility", visibility)
			}
			if limit > 0 {
				q.Set("limit", strconv.Itoa(limit))
			}
			resp, err := routes.c.Get(routes.base, q)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&session, "session", "", assetsSessionFlagHelp)
	cmd.Flags().BoolVar(&all, "all", false, "List the whole org's files, not just this session's (assets:read)")
	cmd.Flags().StringVar(&visibility, "visibility", "", "private or link")
	cmd.Flags().IntVar(&limit, "limit", 0, "Maximum results (1-500, default 100)")
	return cmd
}

func newAssetsGet(rt *Runtime) *cobra.Command {
	var (
		session    string
		attachment bool
	)
	cmd := &cobra.Command{
		Use:   "get <asset_id>",
		Short: "Show a file with a signed download URL valid for an hour (GET .../assets/{id})",
		Long: `Shows one file: "url" (members), "public_url" (when published), and a
signed "download_url" that works for an hour without signing in.

  runtm-api assets get <asset_id>
  runtm-api assets get <asset_id> --attachment | jq -r .download_url | xargs curl -fsSLo out.pdf

Required scope: sessions:read in a session, assets:read for the org.`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			routes, err := resolveAssetRoutes(rt, session, false)
			if err != nil {
				return err
			}
			q := url.Values{}
			if attachment {
				q.Set("disposition", "attachment")
			}
			resp, err := routes.c.Get(routes.item(args[0]), q)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&session, "session", "", assetsSessionFlagHelp)
	cmd.Flags().BoolVar(&attachment, "attachment", false, "Sign the URL to download the file instead of opening it")
	return cmd
}

func newAssetsPublish(rt *Runtime) *cobra.Command {
	var session, note string
	cmd := &cobra.Command{
		Use:   "publish <asset_id>...",
		Short: "Make files public to anyone with the link (approval in a session)",
		Long: `Makes files open to anyone with the link, for prospects or a public website.

Inside a session this asks a person instead: it opens one approval listing
the files (on the session card and in the Slack, Teams or Linear thread the
session came from) and returns status "pending". End your turn; you are
resumed with the decision. Once approved, "public_url" is set on each file.
On a rejection, tell the person and stop.

  runtm-api assets publish <asset_id> <asset_id> --note "Kushki follow-up deck and one-pager"

Outside a session a person's org key publishes at once.

Required scope: sessions:write in a session, assets:write outside one.`,
		Args: cobra.MinimumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			routes, err := resolveAssetRoutes(rt, session, false)
			if err != nil {
				return err
			}
			if routes.session != "" {
				resp, err := routes.c.PostJSON(routes.base+"/publish", map[string]any{
					"asset_ids": args,
					"note":      note,
				})
				if err != nil {
					return runJSON(rt, resp, err)
				}
				var out map[string]any
				if json.Unmarshal(resp, &out) != nil || out["status"] != "pending" {
					rt.WriteJSON(resp)
					return nil
				}
				out["next"] = "A person has to approve this in Runtime. End your turn; the decision arrives as your next message. Then run 'runtm-api assets get <asset_id>' for public_url."
				rt.WriteObject(out)
				return nil
			}
			assets := make([]json.RawMessage, 0, len(args))
			for _, id := range args {
				resp, err := routes.c.PatchJSON(routes.item(id), map[string]any{"visibility": "link"})
				if err != nil {
					return runJSON(rt, resp, err)
				}
				assets = append(assets, json.RawMessage(resp))
			}
			rt.WriteObject(map[string]any{"status": "public", "approval_id": nil, "assets": assets})
			return nil
		},
	}
	cmd.Flags().StringVar(&session, "session", "", assetsSessionFlagHelp)
	cmd.Flags().StringVar(&note, "note", "", "Why these files should be public; shown to the approver")
	return cmd
}

func newAssetsUnpublish(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "unpublish <asset_id>",
		Short: "Make a file private again (PATCH /api/assets/{id})",
		Long: `Makes a published file private again. Its public link stops working at once;
publishing it later issues a new link.

Only a person can do this.

Required scope: assets:write.`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, "assets")
			if err != nil {
				return err
			}
			resp, err := c.PatchJSON("/assets/"+url.PathEscape(args[0]), map[string]any{"visibility": "private"})
			return runJSON(rt, resp, err)
		},
	}
}

func newAssetsDelete(rt *Runtime) *cobra.Command {
	var yes bool
	cmd := &cobra.Command{
		Use:   "delete <asset_id>",
		Short: "Delete a file and its links (DELETE /api/assets/{id})",
		Long: `Deletes a file. Its member and public links stop working; storage keeps
the bytes restorable for a week.

Only a person can do this.

Required scope: assets:write.`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if !yes {
				rt.WriteObject(map[string]any{
					"error": "Destructive operation requires --yes to confirm.",
					"hint":  "Deleting breaks every link to the file, including public ones. Pass --yes when you are sure.",
				})
				return errSilent
			}
			c, _, err := requireOrgClient(rt, "assets")
			if err != nil {
				return err
			}
			resp, err := c.Delete("/assets/" + url.PathEscape(args[0]))
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().BoolVar(&yes, "yes", false, "Confirm deletion")
	return cmd
}
