package cmd

import (
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/spf13/cobra"
)

// defaultDocsBaseURL is where the public documentation is served. Every page
// is available as plain markdown by appending ".md" to its path, and
// "llms.txt" is the index (one line per page: title, URL, description).
const defaultDocsBaseURL = "https://docs.runtm.com"

// NewDocsCommand returns `runtm-api docs` -- fetch the public documentation
// as markdown so agents running in sandboxes without a web-fetch tool can
// still read it. This is the fallback path the bundled SKILL.md points at.
//
// Unlike every other command this prints markdown, not JSON, because the
// consumer is the agent reading it, not a program parsing it. Errors still
// go to stderr as JSON via the normal runtime path.
func NewDocsCommand() *cobra.Command {
	var base string
	cmd := &cobra.Command{
		Use:   "docs [path]",
		Short: "Print a documentation page (or the llms.txt index) as markdown",
		Long: `Fetches the public docs at https://docs.runtm.com as plain markdown.

With no argument it prints llms.txt, the index of every page with a one-line
description of when to read it. With a path it prints that page:

  runtm-api docs                        # the index
  runtm-api docs build/overview         # one page (".md" is appended for you)
  runtm-api docs guides/payments/support-agent.md

Build and Guides pages carry the CLI equivalent of each dashboard step in
agent-only blocks that appear only in this markdown output: "cli:" lines to
run, "cli-verify:" lines to check, and "cli-handoff:" URLs to give a person
(for example to enter a credential, which an agent must never do itself).

Override the base URL with --base or RUNTM_DOCS_URL.`,
		Args: cobra.MaximumNArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			if base == "" {
				base = os.Getenv("RUNTM_DOCS_URL")
			}
			if base == "" {
				base = defaultDocsBaseURL
			}
			base = strings.TrimRight(base, "/")

			target := base + "/llms.txt"
			if len(args) == 1 {
				p := strings.Trim(strings.TrimSpace(args[0]), "/")
				if p == "" {
					return fmt.Errorf("docs: empty path; run `runtm-api docs` for the index")
				}
				if p != "llms.txt" && p != "llms-full.txt" && !strings.HasSuffix(p, ".md") {
					p += ".md"
				}
				target = base + "/" + p
			}

			httpClient := &http.Client{Timeout: 30 * time.Second}
			req, err := http.NewRequestWithContext(cmd.Context(), http.MethodGet, target, nil)
			if err != nil {
				return err
			}
			req.Header.Set("Accept", "text/markdown, text/plain;q=0.9, */*;q=0.1")
			req.Header.Set("User-Agent", "runtm-api/"+Version)
			resp, err := httpClient.Do(req)
			if err != nil {
				return fmt.Errorf("docs: fetch %s: %w", target, err)
			}
			defer resp.Body.Close()
			if resp.StatusCode == http.StatusNotFound {
				return fmt.Errorf("docs: no page at %s (run `runtm-api docs` for the index)", target)
			}
			if resp.StatusCode < 200 || resp.StatusCode >= 300 {
				return fmt.Errorf("docs: %s returned HTTP %d", target, resp.StatusCode)
			}
			body, err := io.ReadAll(io.LimitReader(resp.Body, 8<<20))
			if err != nil {
				return fmt.Errorf("docs: read %s: %w", target, err)
			}
			_, err = os.Stdout.Write(body)
			return err
		},
	}
	cmd.Flags().StringVar(&base, "base", "", "Docs base URL (default https://docs.runtm.com, also RUNTM_DOCS_URL)")
	return cmd
}
