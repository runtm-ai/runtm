package cmd

import (
	"fmt"
	"net/url"
	"strconv"

	"github.com/spf13/cobra"
)

// NewGithubCommand returns `runtm-api github` for GitHub App installation and
// repo-access introspection. Repo access is the precondition for most
// template work, so an agent needs to see which installations exist and what
// they can reach before it can diagnose a failed clone or template build.
//
// The App INSTALL itself is browser-bound (GitHub's flow); create the app
// with 'runtm-api agents create --type github'.
//
// Routes:
//
//	GET  /api/github-app/installations                       integrations:read
//	GET  /api/github-app/repos                               integrations:read
//	GET  /api/github-app/installations/{id}/repos            integrations:read
//	POST /api/github-app/installations/{id}/add-repo         integrations:write
func NewGithubCommand(rt *Runtime) *cobra.Command {
	cmd := &cobra.Command{
		Use:   "github",
		Short: "Inspect GitHub App installations and the repos they can reach",
		Long: `Read the org's GitHub App installations and their accessible repos, and
grant the App access to another repo.

  runtm-api github installations
  runtm-api github repos
  runtm-api github repos --installation <installation_uuid>
  runtm-api github add-repo <installation_uuid> --repo-id 123 --repo owner/name --oauth-token <tok>

Installation ids here are Runtm's UUIDs (from 'installations'), not GitHub's
numeric ids.`,
	}
	cmd.AddCommand(
		newGithubInstallations(rt),
		newGithubRepos(rt),
		newGithubAddRepo(rt),
	)
	return cmd
}

func newGithubInstallations(rt *Runtime) *cobra.Command {
	return &cobra.Command{
		Use:   "installations",
		Short: "List GitHub App installations (GET /api/github-app/installations)",
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			resp, err := c.Get("/github-app/installations", nil)
			return runJSON(rt, resp, err)
		},
	}
}

func newGithubRepos(rt *Runtime) *cobra.Command {
	var (
		installation string
		page         int
		perPage      int
	)
	cmd := &cobra.Command{
		Use:   "repos",
		Short: "List repos the App can reach (GET /api/github-app/repos)",
		Long: `Without --installation, lists every installation with its repos in one
call. With --installation <uuid>, pages through that installation's repos.`,
		RunE: func(cmd *cobra.Command, args []string) error {
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			q := url.Values{}
			if perPage > 0 {
				q.Set("per_page", strconv.Itoa(perPage))
			}
			if installation == "" {
				resp, err := c.Get("/github-app/repos", q)
				return runJSON(rt, resp, err)
			}
			if page > 0 {
				q.Set("page", strconv.Itoa(page))
			}
			path := "/github-app/installations/" + url.PathEscape(installation) + "/repos"
			resp, err := c.Get(path, q)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().StringVar(&installation, "installation", "", "Installation UUID to page through (default: all installations)")
	cmd.Flags().IntVar(&page, "page", 0, "Page number (with --installation)")
	cmd.Flags().IntVar(&perPage, "per-page", 0, "Results per page (1-100)")
	return cmd
}

func newGithubAddRepo(rt *Runtime) *cobra.Command {
	var (
		repoID     int
		repoName   string
		oauthToken string
	)
	cmd := &cobra.Command{
		Use:   "add-repo <installation_uuid>",
		Short: "Grant the App access to a repo (POST .../installations/{id}/add-repo)",
		Long: `Adds a repository to a GitHub App installation's access list. Requires a
GitHub user OAuth token with repo scope (GitHub's API demands a user grant
for this; the App token cannot extend its own access). The response may be
200 with success=false when GitHub declines, so read the message.`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if repoID == 0 || repoName == "" || oauthToken == "" {
				return fmt.Errorf("--repo-id, --repo, and --oauth-token are required")
			}
			c, _, err := rt.Client()
			if err != nil {
				return err
			}
			body := map[string]any{
				"repo_id":        repoID,
				"repo_full_name": repoName,
				"oauth_token":    oauthToken,
			}
			path := "/github-app/installations/" + url.PathEscape(args[0]) + "/add-repo"
			resp, err := c.PostJSON(path, body)
			return runJSON(rt, resp, err)
		},
	}
	cmd.Flags().IntVar(&repoID, "repo-id", 0, "GitHub's numeric repository id (required)")
	cmd.Flags().StringVar(&repoName, "repo", "", "Repository owner/name (required)")
	cmd.Flags().StringVar(&oauthToken, "oauth-token", "", "GitHub user OAuth token with repo scope (required)")
	_ = cmd.MarkFlagRequired("repo-id")
	_ = cmd.MarkFlagRequired("repo")
	_ = cmd.MarkFlagRequired("oauth-token")
	return cmd
}
