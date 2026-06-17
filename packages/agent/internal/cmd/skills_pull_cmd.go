package cmd

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"

	"github.com/runtm-ai/runtm/packages/agent/internal/client"
	"github.com/spf13/cobra"
)

// `runtm-api skills pull <template-id>` downloads every skill attached to a
// template and writes it to disk, mirroring the layout a session gets when it
// launches from that template (<target>/<skill-name>/SKILL.md). It is the
// cloud counterpart to `skills install` (which only writes this binary's own
// bundled skills) — here the files come from the org's skills via the API.

// pulledSkillFile mirrors the SkillFile fields we need to write a file.
type pulledSkillFile struct {
	Path   string  `json:"path"`
	Mode   string  `json:"mode"`
	Inline *string `json:"inline"`
	Ref    *string `json:"ref"`
	URL    *string `json:"url"`
}

type pulledSkillContent struct {
	EntryMd     string            `json:"entry_md"`
	Files       []pulledSkillFile `json:"files"`
	Frontmatter map[string]any    `json:"frontmatter"`
}

// pulledSkill is the subset of DirectiveResource the pull command reads.
type pulledSkill struct {
	ID          string              `json:"id"`
	Name        string              `json:"name"`
	DisplayName string              `json:"display_name"`
	Description string              `json:"description"`
	Content     *pulledSkillContent `json:"content"`
}

type pullListResp struct {
	Directives    []pulledSkill `json:"directives"`
	NextPageToken string        `json:"next_page_token"`
	TotalSize     int           `json:"total_size"`
}

func newSkillsPull(rt *Runtime) *cobra.Command {
	var (
		target string
		agents []string
		repos  []string
		force  bool
	)
	cmd := &cobra.Command{
		Use:   "pull <template-id>",
		Short: "Download a template's skills and write them to disk",
		Long: `Download every skill attached to a template and write each one — its
SKILL.md plus any bundled files — into the agent skills directories, exactly
where a session gets them when it launches from the template:

  ~/.claude/skills/<skill-name>/SKILL.md   (claude-code)
  ~/.agents/skills/<skill-name>/SKILL.md   (codex)

By default writes the claude-code layout (~/.claude/skills). Pass --agent codex
for ~/.agents/skills, --agent all for both (repeat --agent to combine), or
--target to write to one explicit directory instead. Existing skill directories
are skipped unless --force is passed.

Org-scoped: pass --org or RUNTM_ORG_ID, or use an org-scoped key. Needs the
context:read scope.

Example:
  runtm-api skills pull <template_id>
  runtm-api skills pull <template_id> --agent claude-code --agent codex --force`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			templateID := args[0]

			roots, err := resolvePullTargets(target, agents)
			if err != nil {
				return err
			}

			c, _, err := requireOrgClient(rt, "skills")
			if err != nil {
				return err
			}

			skillsList, err := fetchTemplateSkills(c, templateID, repos)
			if err != nil {
				return err
			}

			written := []map[string]any{}
			skipped := []map[string]any{}
			for _, root := range roots {
				for _, s := range skillsList {
					skillDir := filepath.Join(root, sanitizeSkillName(s.Name))
					if !force {
						if _, statErr := os.Stat(skillDir); statErr == nil {
							skipped = append(skipped, map[string]any{
								"skill":  s.Name,
								"path":   skillDir,
								"reason": "directory exists (pass --force to overwrite)",
							})
							continue
						}
					}
					files, werr := writeSkill(c, s, skillDir)
					if werr != nil {
						return fmt.Errorf("write skill %q: %w", s.Name, werr)
					}
					written = append(written, map[string]any{
						"skill": s.Name,
						"path":  skillDir,
						"files": files,
					})
				}
			}

			rt.WriteObject(map[string]any{
				"pulled":      true,
				"template_id": templateID,
				"targets":     roots,
				"count":       len(written),
				"skills":      written,
				"skipped":     skipped,
			})
			return nil
		},
	}
	cmd.Flags().StringVar(&target, "target", "", "Write to one explicit directory instead of the per-agent home defaults")
	cmd.Flags().StringArrayVar(&agents, "agent", nil, "Agent skills layout: claude-code (~/.claude/skills), codex (~/.agents/skills), or all (repeatable; default claude-code)")
	cmd.Flags().StringArrayVar(&repos, "repo", nil, "Also include skills attached to this repo (owner/name); repeatable. Matches how a template resolves its repo-scoped skills")
	cmd.Flags().BoolVar(&force, "force", false, "Overwrite existing skill directories")
	return cmd
}

// resolvePullTargets returns the destination skill roots. An explicit --target
// wins; otherwise it maps each requested agent to its home-based skills
// directory, matching where the server materializes skills into a sandbox
// (~/.claude/skills for claude-code, ~/.agents/skills for codex).
func resolvePullTargets(override string, agents []string) ([]string, error) {
	if override != "" {
		return []string{override}, nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return nil, fmt.Errorf("resolve home directory: %w", err)
	}
	if len(agents) == 0 {
		agents = []string{"claude-code"}
	}

	seen := map[string]bool{}
	var roots []string
	add := func(rel string) {
		dir := filepath.Join(home, filepath.FromSlash(rel))
		if !seen[dir] {
			seen[dir] = true
			roots = append(roots, dir)
		}
	}
	for _, a := range agents {
		switch a {
		case "", "claude", "claude-code":
			add(".claude/skills")
		case "codex":
			add(".agents/skills")
		case "all":
			add(".claude/skills")
			add(".agents/skills")
		default:
			return nil, fmt.Errorf("invalid --agent %q (expected claude-code, codex, or all)", a)
		}
	}
	return roots, nil
}

// fetchTemplateSkills lists every skill that materializes into a template,
// matching the server's resolution: applies-to-all + template-scoped +
// repo-scoped attachments. The backend list filter always folds in
// applies_to_all, but template_id and repo_full_name narrow within a single
// call, so each scope is queried separately and merged (deduped by id).
func fetchTemplateSkills(c *client.Client, templateID string, repos []string) ([]pulledSkill, error) {
	byID := map[string]pulledSkill{}
	order := []string{}

	addScope := func(scope func(url.Values)) error {
		pageToken := ""
		for {
			q := url.Values{}
			q.Set("type_family", "skill")
			q.Set("include_content", "true")
			q.Set("page_size", "100")
			scope(q)
			if pageToken != "" {
				q.Set("page_token", pageToken)
			}
			raw, err := c.Get(directivesListPath, q)
			if err != nil {
				return err
			}
			var resp pullListResp
			if err := json.Unmarshal(raw, &resp); err != nil {
				return fmt.Errorf("parse skills list: %w", err)
			}
			for _, s := range resp.Directives {
				if _, ok := byID[s.ID]; !ok {
					order = append(order, s.ID)
				}
				byID[s.ID] = s
			}
			if resp.NextPageToken == "" {
				break
			}
			pageToken = resp.NextPageToken
		}
		return nil
	}

	if err := addScope(func(q url.Values) { q.Set("template_id", templateID) }); err != nil {
		return nil, err
	}
	for _, repo := range repos {
		if strings.TrimSpace(repo) == "" {
			continue
		}
		repo := repo
		if err := addScope(func(q url.Values) { q.Set("repo_full_name", repo) }); err != nil {
			return nil, err
		}
	}

	out := make([]pulledSkill, 0, len(order))
	for _, id := range order {
		out = append(out, byID[id])
	}
	return out, nil
}

// writeSkill writes one skill's files into skillDir and returns the paths written.
func writeSkill(c *client.Client, s pulledSkill, skillDir string) ([]string, error) {
	if s.Content == nil {
		return nil, fmt.Errorf("no content returned (the skill may be unavailable, or use a context:read key)")
	}
	if err := os.MkdirAll(skillDir, 0o755); err != nil {
		return nil, err
	}
	entry := s.Content.EntryMd
	if entry == "" {
		entry = "SKILL.md"
	}
	var written []string
	for _, f := range s.Content.Files {
		data, err := resolveFileBytes(c, f)
		if err != nil {
			return nil, fmt.Errorf("file %q: %w", f.Path, err)
		}
		// The entry markdown must carry name/description frontmatter to be a
		// valid on-disk skill; reconstruct it the way the server does at
		// materialization time when it's missing.
		if isEntryFile(f, entry) && (f.Mode == "" || f.Mode == "text") {
			data = []byte(ensureSkillFrontmatter(s, s.Content, string(data)))
		}
		dest := filepath.Join(skillDir, filepath.FromSlash(cleanRelPath(f.Path)))
		if err := os.MkdirAll(filepath.Dir(dest), 0o755); err != nil {
			return nil, err
		}
		if err := os.WriteFile(dest, data, 0o644); err != nil {
			return nil, err
		}
		written = append(written, f.Path)
	}
	return written, nil
}

// resolveFileBytes returns a file's bytes from inline content or, for files
// stored in object storage, by downloading the presigned URL.
func resolveFileBytes(c *client.Client, f pulledSkillFile) ([]byte, error) {
	if f.Inline != nil {
		if f.Mode == "binary" {
			decoded, err := base64.StdEncoding.DecodeString(*f.Inline)
			if err != nil {
				return nil, fmt.Errorf("decode binary inline: %w", err)
			}
			return decoded, nil
		}
		return []byte(*f.Inline), nil
	}
	if f.URL != nil && *f.URL != "" {
		return downloadURL(*f.URL)
	}
	return nil, fmt.Errorf("file has neither inline content nor a download URL")
}

// downloadURL fetches a presigned object-storage URL with a plain client (no
// API auth headers — the URL is already signed).
func downloadURL(rawURL string) ([]byte, error) {
	resp, err := http.Get(rawURL) //nolint:gosec // presigned URL minted by our API
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("download failed: HTTP %d", resp.StatusCode)
	}
	return io.ReadAll(resp.Body)
}

func isEntryFile(f pulledSkillFile, entry string) bool {
	p := strings.TrimPrefix(f.Path, "./")
	return p == "SKILL.md" || p == strings.TrimPrefix(entry, "./")
}

// cleanRelPath neutralizes any leading "./" and ".." escapes so a skill file
// path can never be written outside its skill directory.
func cleanRelPath(p string) string {
	cleaned := filepath.Clean(string(filepath.Separator) + filepath.FromSlash(p))
	return strings.TrimPrefix(cleaned, string(filepath.Separator))
}

// sanitizeSkillName keeps skill directory names to a single safe path segment.
func sanitizeSkillName(name string) string {
	name = strings.ReplaceAll(name, "..", "-")
	name = strings.ReplaceAll(name, "/", "-")
	name = strings.ReplaceAll(name, string(filepath.Separator), "-")
	if strings.TrimSpace(name) == "" {
		return "skill"
	}
	return name
}

// ensureSkillFrontmatter mirrors the server's materialization step: if the
// SKILL.md already has name + description frontmatter it's returned untouched;
// otherwise frontmatter is synthesized from stored frontmatter and the
// directive's own metadata.
func ensureSkillFrontmatter(s pulledSkill, content *pulledSkillContent, markdown string) string {
	existing, body, _ := parseSimpleFrontmatter(markdown)
	if existing["name"] != "" && existing["description"] != "" {
		return markdown
	}

	name := firstNonEmpty(existing["name"], stringFromAny(content.Frontmatter["name"]), s.Name)
	if name == "" {
		name = "skill"
	}
	desc := firstNonEmpty(
		existing["description"],
		stringFromAny(content.Frontmatter["description"]),
		s.Description,
		s.DisplayName,
	)
	if desc == "" {
		desc = "Use this skill when working with " + name + "."
	}

	var b strings.Builder
	b.WriteString("---\n")
	b.WriteString("name: " + yamlScalar(name) + "\n")
	b.WriteString("description: " + yamlScalar(desc) + "\n")
	b.WriteString("---\n\n")
	b.WriteString(strings.TrimLeft(body, "\n"))
	return b.String()
}

// parseSimpleFrontmatter does a best-effort read of leading `--- ... ---` YAML,
// enough to tell whether name/description are present. It is deliberately
// minimal (scalar key: value pairs only).
func parseSimpleFrontmatter(markdown string) (map[string]string, string, bool) {
	fm := map[string]string{}
	if !strings.HasPrefix(markdown, "---\n") && !strings.HasPrefix(markdown, "---\r\n") {
		return fm, markdown, false
	}
	nl := strings.IndexByte(markdown, '\n')
	rest := markdown[nl+1:]
	end := strings.Index(rest, "\n---")
	if end < 0 {
		return fm, markdown, false
	}
	header := rest[:end]
	body := strings.TrimLeft(rest[end+len("\n---"):], "\r\n")
	for _, line := range strings.Split(header, "\n") {
		line = strings.TrimRight(line, "\r")
		trimmed := strings.TrimSpace(line)
		if trimmed == "" || strings.HasPrefix(trimmed, "#") {
			continue
		}
		k, v, ok := strings.Cut(line, ":")
		if !ok {
			continue
		}
		fm[strings.TrimSpace(k)] = strings.Trim(strings.TrimSpace(v), `"'`)
	}
	return fm, body, true
}

func firstNonEmpty(vals ...string) string {
	for _, v := range vals {
		if strings.TrimSpace(v) != "" {
			return v
		}
	}
	return ""
}

func stringFromAny(v any) string {
	s, _ := v.(string)
	return s
}

// yamlScalar renders a string as a YAML scalar, quoting + escaping only when
// the raw value could be misparsed.
func yamlScalar(v string) string {
	if v == "" {
		return `""`
	}
	needsQuote := strings.ContainsAny(v, ":#\n\"'{}[]&*?|<>=!%@`,") ||
		strings.HasPrefix(v, " ") || strings.HasSuffix(v, " ")
	if !needsQuote {
		return v
	}
	escaped := strings.ReplaceAll(v, `\`, `\\`)
	escaped = strings.ReplaceAll(escaped, `"`, `\"`)
	escaped = strings.ReplaceAll(escaped, "\n", `\n`)
	return `"` + escaped + `"`
}
