package cmd

import (
	"strings"
	"testing"

	"github.com/spf13/cobra"
)

// findSub returns the named subcommand, or fails the test.
func findSub(t *testing.T, parent *cobra.Command, name string) *cobra.Command {
	t.Helper()
	for _, c := range parent.Commands() {
		if c.Name() == name {
			return c
		}
	}
	t.Fatalf("%s has no %q subcommand", parent.Name(), name)
	return nil
}

func TestScheduledAgentsIsRegisteredOnRoot(t *testing.T) {
	root := NewRootCommand()
	cmd := findSub(t, root, "scheduled-agents")
	// The automation primitive is easy to look for under several names.
	for _, alias := range []string{"schedules", "cron"} {
		if !cmd.HasAlias(alias) {
			t.Errorf("scheduled-agents should answer to %q", alias)
		}
	}
}

func TestScheduledAgentsSubcommands(t *testing.T) {
	cmd := NewScheduledAgentsCommand(newTestRuntime())
	for _, name := range []string{"list", "get", "create", "update", "run-now", "delete"} {
		findSub(t, cmd, name)
	}
}

func TestRunNowAliases(t *testing.T) {
	runNow := findSub(t, NewScheduledAgentsCommand(newTestRuntime()), "run-now")
	for _, alias := range []string{"run", "trigger"} {
		if !runNow.HasAlias(alias) {
			t.Errorf("run-now should answer to %q", alias)
		}
	}
}

func TestScheduledAgentPath(t *testing.T) {
	if got := scheduledAgentPath("abc-123"); got != "/v1/scheduled-agents/abc-123" {
		t.Errorf("path = %q", got)
	}
	// Ids land in the URL path; they must be escaped.
	if got := scheduledAgentPath("a b/c"); !strings.Contains(got, "a%20b%2Fc") {
		t.Errorf("path = %q, want the id escaped", got)
	}
}

// --- flag → request body --------------------------------------------------

// bodyFor parses argv against a scheduled-agent flag set and returns the
// request body it would send.
func bodyFor(t *testing.T, argv []string, onlyChanged bool) map[string]any {
	t.Helper()
	f := &scheduledAgentFlags{}
	cmd := &cobra.Command{Use: "x", RunE: func(*cobra.Command, []string) error { return nil }}
	f.register(cmd)
	if err := cmd.Flags().Parse(argv); err != nil {
		t.Fatalf("flag parse: %v", err)
	}
	body, err := f.body(cmd, onlyChanged)
	if err != nil {
		t.Fatalf("body: %v", err)
	}
	return body
}

func TestUpdateSendsOnlyChangedFields(t *testing.T) {
	// A partial edit must not clobber the prompt or cron it never mentioned.
	body := bodyFor(t, []string{"--enabled"}, true)
	if len(body) != 1 {
		t.Fatalf("body = %#v, want only enabled", body)
	}
	if body["enabled"] != true {
		t.Errorf("enabled = %v, want true", body["enabled"])
	}
}

func TestDisabledFlagSetsEnabledFalse(t *testing.T) {
	body := bodyFor(t, []string{"--disabled"}, true)
	if body["enabled"] != false {
		t.Errorf("enabled = %v, want false", body["enabled"])
	}
}

func TestTemplateFlagMapsToOrgTemplateID(t *testing.T) {
	body := bodyFor(t, []string{"--template", "tpl-1"}, true)
	if body["org_template_id"] != "tpl-1" {
		t.Errorf("org_template_id = %v", body["org_template_id"])
	}
}

func TestSlackTargetFlagsTravelTogether(t *testing.T) {
	f := &scheduledAgentFlags{}
	cmd := &cobra.Command{Use: "x"}
	f.register(cmd)
	if err := cmd.Flags().Parse([]string{"--slack-channel", "C123"}); err != nil {
		t.Fatalf("flag parse: %v", err)
	}
	if _, err := f.body(cmd, true); err == nil {
		t.Fatal("expected an error when only one half of the Slack target is set")
	}
}

func TestSlackTargetBothSet(t *testing.T) {
	body := bodyFor(t, []string{"--slack-integration", "i-1", "--slack-channel", "C123"}, true)
	if body["slack_integration_id"] != "i-1" || body["slack_channel_id"] != "C123" {
		t.Errorf("slack target = %#v", body)
	}
}

func TestEnabledAndDisabledAreMutuallyExclusive(t *testing.T) {
	f := &scheduledAgentFlags{}
	cmd := &cobra.Command{Use: "x"}
	f.register(cmd)
	if err := cmd.Flags().Parse([]string{"--enabled", "--disabled"}); err != nil {
		t.Fatalf("flag parse: %v", err)
	}
	if _, err := f.body(cmd, true); err == nil {
		t.Fatal("expected an error for --enabled with --disabled")
	}
}

func TestCreateSendsRequiredFieldsEvenWhenUnchanged(t *testing.T) {
	body := bodyFor(t, []string{"--name", "weekly", "--cron", "0 18 * * 1", "--prompt", "go"}, false)
	for _, key := range []string{"name", "cron", "prompt"} {
		if _, ok := body[key]; !ok {
			t.Errorf("create body missing %q: %#v", key, body)
		}
	}
}

// --- skills / mcp scoping -------------------------------------------------

func TestSkillsListAcceptsTemplateScope(t *testing.T) {
	// The whole point: someone reaching for `skills list` should be able to
	// ask the template question without knowing `template skills` exists.
	list := findSub(t, NewSkillsCommand(newTestRuntime()), "list")
	if list.Flags().Lookup("template") == nil {
		t.Fatal("skills list must accept --template")
	}
	if list.Flags().Lookup("repo") == nil {
		t.Fatal("skills list must accept --repo")
	}
}

func TestMcpListAcceptsTemplateScope(t *testing.T) {
	list := findSub(t, NewMcpCommand(newTestRuntime()), "list")
	if list.Flags().Lookup("template") == nil {
		t.Fatal("mcp list must accept --template")
	}
}
