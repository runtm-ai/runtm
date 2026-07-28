package cmd

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"time"

	"github.com/spf13/cobra"
)

// Directive lifecycle verbs shared by `skills` and `mcp`: bulk import, repo
// discovery, resync, lock/unlock, facets, and content-addressed file upload.
// These are the difference between authoring skills one at a time by hand and
// operating on them at org scale.
//
// Routes (all under /api/agent-directives):
//
//	POST :import                          context:write
//	POST :discover-in-repo                context:read
//	POST /{id}:resync                     context:write
//	POST /{id}:lock | :unlock             context:write (org admin)
//	GET  :facets                          context:read
//	POST /{id}/files:presign-upload       context:write

func newDirectiveImport(rt *Runtime, singular string) *cobra.Command {
	var (
		sourceKind      string
		uri             string
		ref             string
		paths           []string
		attachTemplates []string
		attachRepos     []string
		attachAll       bool
	)
	cmd := &cobra.Command{
		Use:   "import",
		Short: fmt.Sprintf("Import %ss from a repo or URL (POST /api/agent-directives:import)", singular),
		Long: fmt.Sprintf(`Bulk-import %ss instead of creating them one at a time.

  --source github_repo --uri owner/repo [--path skills/a/SKILL.md ...]
      import from a GitHub repo (every discovered skill, or just --path entries)
  --source github_url --uri https://github.com/owner/repo/blob/main/SKILL.md
      import one file by GitHub URL
  --source url --uri https://example.com/SKILL.md
      import one file by raw URL

Pass --attach-template/--attach-repo/--attach-all to attach every imported
item in the same call, so nothing lands unattached and silently unused.
Pair with '%s discover --repo owner/repo' to see candidates first.`, singular, singular),
		RunE: func(cmd *cobra.Command, args []string) error {
			if sourceKind == "" || uri == "" {
				return fmt.Errorf("--source and --uri are required")
			}
			body := map[string]any{
				"source_kind": sourceKind,
				"uri":         uri,
			}
			if ref != "" {
				body["ref"] = ref
			}
			if len(paths) > 0 {
				body["paths"] = paths
			}
			if attachAll || len(attachTemplates) > 0 || len(attachRepos) > 0 {
				if attachAll && (len(attachTemplates) > 0 || len(attachRepos) > 0) {
					return fmt.Errorf("--attach-all cannot be combined with --attach-template or --attach-repo")
				}
				body["attach"] = map[string]any{
					"template_ids":    nonNil(attachTemplates),
					"repo_full_names": nonNil(attachRepos),
					"applies_to_all":  attachAll,
				}
			}
			c, _, err := requireOrgClient(rt, singular+"s")
			if err != nil {
				return err
			}
			resp, err := c.PostJSON("/agent-directives:import", body)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&sourceKind, "source", "", "Import source: github_repo, github_url, or url (required)")
	cmd.Flags().StringVar(&uri, "uri", "", "Repo (owner/repo) or file URL to import from (required)")
	cmd.Flags().StringVar(&ref, "ref", "", "Git ref (branch, tag, or commit) for repo imports")
	cmd.Flags().StringArrayVar(&paths, "path", nil, "github_repo: only import these SKILL.md paths (repeatable)")
	cmd.Flags().StringArrayVar(&attachTemplates, "attach-template", nil, "Attach every imported item to this template id (repeatable)")
	cmd.Flags().StringArrayVar(&attachRepos, "attach-repo", nil, "Attach every imported item to this repo owner/name (repeatable)")
	cmd.Flags().BoolVar(&attachAll, "attach-all", false, "Attach every imported item org-wide")
	_ = cmd.MarkFlagRequired("source")
	_ = cmd.MarkFlagRequired("uri")
	return cmd
}

func newDirectiveDiscover(rt *Runtime) *cobra.Command {
	var (
		repo string
		ref  string
	)
	cmd := &cobra.Command{
		Use:   "discover",
		Short: "Scan a repo for SKILL.md candidates (POST /api/agent-directives:discover-in-repo)",
		Long: `Read-only scan of a GitHub repo for SKILL.md files. Nothing is created;
each candidate reports its path, frontmatter, and whether it was already
imported. Follow up with 'skills import --source github_repo --uri <repo>'.`,
		RunE: func(cmd *cobra.Command, args []string) error {
			if repo == "" {
				return fmt.Errorf("--repo is required")
			}
			body := map[string]any{"repo": repo}
			if ref != "" {
				body["ref"] = ref
			}
			c, _, err := requireOrgClient(rt, "skills")
			if err != nil {
				return err
			}
			resp, err := c.PostJSON("/agent-directives:discover-in-repo", body)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&repo, "repo", "", "GitHub repo in owner/name form (required)")
	cmd.Flags().StringVar(&ref, "ref", "", "Git ref to scan (default: the repo's default branch)")
	_ = cmd.MarkFlagRequired("repo")
	return cmd
}

func newDirectiveResync(rt *Runtime, singular string) *cobra.Command {
	return &cobra.Command{
		Use:   "resync <id>",
		Short: fmt.Sprintf("Re-import a %s from its original source (POST .../{id}:resync)", singular),
		Long: fmt.Sprintf(`Overwrites the %s's content in place from the source it was imported
from, keeping its id and attachments. No-op when the source is unchanged
(the response carries no_changes). Locked items cannot be resynced.`, singular),
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, singular+"s")
			if err != nil {
				return err
			}
			resp, err := c.PostJSON(directivePath(args[0])+":resync", map[string]any{})
			return runJSON(rt, resp, err)
		},
	}
}

func newDirectiveLock(rt *Runtime, singular string, lock bool) *cobra.Command {
	verb := "lock"
	desc := "Lock a %s so only an org admin can edit or delete it"
	if !lock {
		verb = "unlock"
		desc = "Unlock a %s (org admin only)"
	}
	return &cobra.Command{
		Use:   verb + " <id>",
		Short: fmt.Sprintf(desc, singular),
		Long: fmt.Sprintf(`%s. A locked %s still loads into sessions and template builds; only
edits, deletion, resync, and attachment changes are blocked until an org
admin unlocks it. Requires admin or owner role.`, fmt.Sprintf(desc, singular), singular),
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, singular+"s")
			if err != nil {
				return err
			}
			resp, err := c.PostJSON(directivePath(args[0])+":"+verb, map[string]any{})
			return runJSON(rt, resp, err)
		},
	}
}

func newDirectiveFacets(rt *Runtime, singular, typeFamily string) *cobra.Command {
	var (
		repo       string
		templateID string
		labels     []string
	)
	cmd := &cobra.Command{
		Use:   "facets",
		Short: fmt.Sprintf("Label facets over the org's %ss (GET /api/agent-directives:facets)", singular),
		Long: fmt.Sprintf(`Aggregates the label keys and values across %ss, with counts. The same
filters as 'list' apply, so this is how to see what labels exist before
filtering by one.`, singular),
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := requireOrgClient(rt, singular+"s")
			if err != nil {
				return err
			}
			q := url.Values{}
			q.Set("type_family", typeFamily)
			if repo != "" {
				q.Set("repo_full_name", repo)
			}
			if templateID != "" {
				q.Set("template_id", templateID)
			}
			for _, l := range labels {
				q.Add("labels", l)
			}
			resp, err := c.Get("/agent-directives:facets", q)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&repo, "repo", "", "Scope to one repo (owner/name)")
	cmd.Flags().StringVar(&templateID, "template", "", "Scope to one template id")
	cmd.Flags().StringArrayVar(&labels, "label", nil, "Pre-filter by label key:value (repeatable)")
	return cmd
}

// newDirectiveUploadFile implements the full content-addressed upload flow:
// hash the local file, presign, PUT the bytes to storage, then patch the
// directive's file manifest so the new entry points at the stored blob.
func newDirectiveUploadFile(rt *Runtime, singular string) *cobra.Command {
	var (
		localPath string
		entryPath string
		mode      string
	)
	cmd := &cobra.Command{
		Use:   "upload-file <id>",
		Short: fmt.Sprintf("Add or replace a bundled file on a %s via object storage", singular),
		Long: fmt.Sprintf(`Uploads a local file into the %s's bundle without inlining it:

  1. POST /api/agent-directives/{id}/files:presign-upload (sha256 + size)
  2. HTTP PUT of the raw bytes to the presigned URL
  3. PATCH the %s so its file manifest carries {path, ref} for the entry

Use for binary assets or files past the inline size threshold. Max 5 MiB.

  runtm-api %ss upload-file <id> --file ./data/lookup.csv --path data/lookup.csv`, singular, singular, singular),
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if localPath == "" || entryPath == "" {
				return fmt.Errorf("--file and --path are required")
			}
			if mode != "text" && mode != "binary" {
				return fmt.Errorf("--mode must be text or binary")
			}
			data, err := os.ReadFile(localPath) // #nosec G304 -- the named file is the command's argument
			if err != nil {
				return fmt.Errorf("read %s: %w", localPath, err)
			}
			sum := sha256.Sum256(data)
			digest := hex.EncodeToString(sum[:])

			c, _, err := requireOrgClient(rt, singular+"s")
			if err != nil {
				return err
			}

			// 1. Presign.
			presignResp, err := c.PostJSON(directivePath(args[0])+"/files:presign-upload", map[string]any{
				"path":   entryPath,
				"sha256": digest,
				"size":   len(data),
			})
			if err != nil {
				return runJSON(rt, presignResp, err)
			}
			var presign struct {
				URL string `json:"url"`
				Ref string `json:"ref"`
			}
			if jerr := json.Unmarshal(presignResp, &presign); jerr != nil || presign.URL == "" {
				return fmt.Errorf("could not parse presign response: %w", jerr)
			}

			// 2. PUT the raw bytes. Content-Length must equal the signed size.
			req, err := http.NewRequestWithContext(cmd.Context(), http.MethodPut, presign.URL, bytes.NewReader(data))
			if err != nil {
				return err
			}
			req.ContentLength = int64(len(data))
			httpClient := &http.Client{Timeout: 120 * time.Second}
			putResp, err := httpClient.Do(req)
			if err != nil {
				return fmt.Errorf("upload to storage failed: %w", err)
			}
			defer putResp.Body.Close()
			if putResp.StatusCode < 200 || putResp.StatusCode >= 300 {
				return fmt.Errorf("upload to storage failed with status %d", putResp.StatusCode)
			}

			// 3. Rewrite the file manifest: replace the entry at --path or append.
			detailResp, err := c.Get(directivePath(args[0]), url.Values{"include_content": []string{"true"}})
			if err != nil {
				return err
			}
			var detail struct {
				Directive struct {
					Content map[string]any `json:"content"`
				} `json:"directive"`
			}
			if jerr := json.Unmarshal(detailResp, &detail); jerr != nil || detail.Directive.Content == nil {
				return fmt.Errorf("could not read the %s's current content to patch its file list: %w", singular, jerr)
			}
			content := detail.Directive.Content
			entry := map[string]any{"path": entryPath, "mode": mode, "ref": presign.Ref, "inline": nil}
			files, _ := content["files"].([]any)
			replaced := false
			for i, f := range files {
				fm, ok := f.(map[string]any)
				if ok && fm["path"] == entryPath {
					files[i] = entry
					replaced = true
					break
				}
			}
			if !replaced {
				files = append(files, any(entry))
			}
			content["files"] = files

			patchResp, err := c.PatchJSON(directivePath(args[0]), map[string]any{"content": content})
			if err != nil {
				return runJSON(rt, patchResp, err)
			}
			rt.WriteObject(map[string]any{
				"uploaded": true,
				"path":     entryPath,
				"ref":      presign.Ref,
				"size":     len(data),
				"replaced": replaced,
			})
			return nil
		},
	}
	cmd.Flags().StringVar(&localPath, "file", "", "Local file to upload (required)")
	cmd.Flags().StringVar(&entryPath, "path", "", "Path of the entry inside the bundle, e.g. data/lookup.csv (required)")
	cmd.Flags().StringVar(&mode, "mode", "binary", "File mode recorded on the entry: text or binary")
	_ = cmd.MarkFlagRequired("file")
	_ = cmd.MarkFlagRequired("path")
	return cmd
}
