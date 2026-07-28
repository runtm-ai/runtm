package cmd

import (
	"testing"

	"github.com/spf13/cobra"
)

// hasSub reports whether parent has a subcommand with the given name.
func hasSub(parent *cobra.Command, name string) bool {
	for _, c := range parent.Commands() {
		if c.Name() == name {
			return true
		}
	}
	return false
}

func TestRootHasNewTopLevelCommands(t *testing.T) {
	root := NewRootCommand()
	for _, name := range []string{"groups", "deployments", "github"} {
		if !hasSub(root, name) {
			t.Errorf("root is missing %q", name)
		}
	}
}

func TestDeploymentsAliases(t *testing.T) {
	deployments := findSub(t, NewRootCommand(), "deployments")
	for _, alias := range []string{"deploy", "deploys"} {
		if !deployments.HasAlias(alias) {
			t.Errorf("deployments should answer to %q", alias)
		}
	}
	for _, name := range []string{"list", "get", "logs", "destroy"} {
		if !hasSub(deployments, name) {
			t.Errorf("deployments is missing %q", name)
		}
	}
}

func TestGithubSubcommands(t *testing.T) {
	github := findSub(t, NewRootCommand(), "github")
	for _, name := range []string{"installations", "repos", "add-repo"} {
		if !hasSub(github, name) {
			t.Errorf("github is missing %q", name)
		}
	}
}

func TestGroupsUsage(t *testing.T) {
	groups := findSub(t, NewRootCommand(), "groups")
	if !hasSub(groups, "usage") {
		t.Error("groups is missing usage")
	}
}

// --- agents: roster as the default ------------------------------------------

func TestAgentsTypeNoLongerRequired(t *testing.T) {
	// No --type now means "the roster", so the flag must not be required on
	// any of the shared verbs.
	agents := NewAgentsCommand(newTestRuntime())
	for _, name := range []string{"list", "get", "create", "update", "delete"} {
		sub := findSub(t, agents, name)
		flag := sub.Flags().Lookup("type")
		if flag == nil {
			t.Errorf("agents %s lost its --type flag", name)
			continue
		}
		if _, required := flag.Annotations[cobra.BashCompOneRequiredFlag]; required {
			t.Errorf("agents %s still requires --type; roster mode needs it optional", name)
		}
	}
}

func TestAgentsRosterSubcommands(t *testing.T) {
	agents := NewAgentsCommand(newTestRuntime())
	for _, name := range []string{"trigger-credentials", "scorecard"} {
		if !hasSub(agents, name) {
			t.Errorf("agents is missing %q", name)
		}
	}
}

func TestAgentEndpointsForNewTypes(t *testing.T) {
	linear, err := agentEndpointsFor("linear")
	if err != nil {
		t.Fatalf("linear endpoints: %v", err)
	}
	if linear.list != "/v1/linear/integrations" || linear.updateVerb != "POST" {
		t.Errorf("linear endpoints wrong: %+v", linear)
	}
	email, err := agentEndpointsFor("email")
	if err != nil {
		t.Fatalf("email endpoints: %v", err)
	}
	if email.list != "/v1/email/integrations" || email.updateVerb != "PUT" {
		t.Errorf("email endpoints wrong: %+v", email)
	}
	if got := email.item("abc"); got != "/v1/email/integration/abc" {
		t.Errorf("email item path = %q", got)
	}
	if _, err := agentEndpointsFor("bogus"); err == nil {
		t.Error("expected an error for an unknown type")
	}
}

func TestCheckFlagsForModeRejectsMismatchedFlags(t *testing.T) {
	// Roster flags with --type must error, and integration flags without
	// --type must error, so a mismatched edit can never be silently dropped.
	mk := func() *cobra.Command {
		c := &cobra.Command{Use: "x"}
		c.Flags().String("instructions", "", "")
		c.Flags().String("model", "", "")
		return c
	}

	c := mk()
	_ = c.Flags().Set("instructions", "be terse")
	if err := checkFlagsForMode(c, "slack"); err == nil {
		t.Error("roster-only flag with --type should error")
	}
	if err := checkFlagsForMode(c, ""); err != nil {
		t.Errorf("roster-only flag without --type should pass, got %v", err)
	}

	c = mk()
	_ = c.Flags().Set("model", "opus")
	if err := checkFlagsForMode(c, ""); err == nil {
		t.Error("integration-only flag without --type should error")
	}
	if err := checkFlagsForMode(c, "github"); err != nil {
		t.Errorf("integration-only flag with --type should pass, got %v", err)
	}
}

// --- session ops --------------------------------------------------------------

func TestSessionOpsSubcommands(t *testing.T) {
	root := NewRootCommand()
	session := findSub(t, root, "session")
	for _, name := range []string{"search", "grade", "approvals", "load-skills", "load-mcps", "load-tools", "tools"} {
		if !hasSub(session, name) {
			t.Errorf("session is missing %q", name)
		}
	}
	approvals := findSub(t, session, "approvals")
	for _, name := range []string{"list", "resolve"} {
		if !hasSub(approvals, name) {
			t.Errorf("session approvals is missing %q", name)
		}
	}
	file := findSub(t, session, "file")
	for _, name := range []string{"upload", "download"} {
		if !hasSub(file, name) {
			t.Errorf("session file is missing %q", name)
		}
	}
}

func TestApprovalsResolveRequiresExactlyOneDecision(t *testing.T) {
	run := func(args ...string) error {
		cmd := newSessionApprovalsResolve(newTestRuntime())
		cmd.SetArgs(args)
		cmd.SilenceErrors = true
		cmd.SilenceUsage = true
		return cmd.Execute()
	}
	if err := run("sess-1", "app-1"); err == nil {
		t.Error("neither --approve nor --reject should error")
	}
	if err := run("sess-1", "app-1", "--approve", "--reject"); err == nil {
		t.Error("both --approve and --reject should error")
	}
}

// --- directives lifecycle ------------------------------------------------------

func TestSkillLifecycleSubcommands(t *testing.T) {
	skills := NewSkillsCommand(newTestRuntime())
	for _, name := range []string{"import", "discover", "resync", "lock", "unlock", "facets", "upload-file"} {
		if !hasSub(skills, name) {
			t.Errorf("skills is missing %q", name)
		}
	}
	mcp := NewMcpCommand(newTestRuntime())
	for _, name := range []string{"resync", "lock", "unlock", "facets"} {
		if !hasSub(mcp, name) {
			t.Errorf("mcp is missing %q", name)
		}
	}
}

func TestOwnerTeamFlagPresent(t *testing.T) {
	skills := NewSkillsCommand(newTestRuntime())
	if findSub(t, skills, "update").Flags().Lookup("owner-team") == nil {
		t.Error("skills update is missing --owner-team")
	}
	mcp := NewMcpCommand(newTestRuntime())
	if findSub(t, mcp, "update").Flags().Lookup("owner-team") == nil {
		t.Error("mcp update is missing --owner-team")
	}
	tmpl := NewTemplateCommand(newTestRuntime())
	up := findSub(t, tmpl, "update")
	if up.Flags().Lookup("owner-team") == nil {
		t.Error("template update is missing --owner-team")
	}
	if up.Flags().Lookup("rebuild-schedule") == nil {
		t.Error("template update is missing --rebuild-schedule")
	}
}

func TestOwnerTeamValueTriState(t *testing.T) {
	if v := ownerTeamValue("team-1"); v != "team-1" {
		t.Errorf("ownerTeamValue(team-1) = %v", v)
	}
	if v := ownerTeamValue(""); v != nil {
		t.Errorf("ownerTeamValue(\"\") = %v, want nil (explicit null clears)", v)
	}
}

// --- guardrails + template sub-resources ---------------------------------------

func TestGuardrailDirectiveFamilies(t *testing.T) {
	guardrails := NewGuardrailsCommand(newTestRuntime())
	for _, family := range []string{"rules", "hooks", "network"} {
		fam := findSub(t, guardrails, family)
		for _, name := range []string{"list", "get", "create", "update", "delete", "attach", "detach", "attachments", "lock", "unlock"} {
			if !hasSub(fam, name) {
				t.Errorf("guardrails %s is missing %q", family, name)
			}
		}
	}
}

func TestTemplateContextAndGuardrails(t *testing.T) {
	tmpl := NewTemplateCommand(newTestRuntime())
	ctx := findSub(t, tmpl, "context")
	for _, name := range []string{"get", "set", "resolve"} {
		if !hasSub(ctx, name) {
			t.Errorf("template context is missing %q", name)
		}
	}
	guardrails := findSub(t, tmpl, "guardrails")
	for _, name := range []string{"list", "create", "update", "delete", "resolve"} {
		if !hasSub(guardrails, name) {
			t.Errorf("template guardrails is missing %q", name)
		}
	}
}

func TestGuardrailTypeMapping(t *testing.T) {
	cases := map[string]string{
		"allowlist": "allowlist_rule_v0",
		"hook":      "hook_v0",
		"network":   "network_rule_v0",
	}
	for family, want := range cases {
		got, err := guardrailTypeFor(family)
		if err != nil || got != want {
			t.Errorf("guardrailTypeFor(%q) = %q, %v; want %q", family, got, err, want)
		}
	}
	if _, err := guardrailTypeFor("bogus"); err == nil {
		t.Error("unknown family should error")
	}
	if _, err := guardrailTypeFor(""); err == nil {
		t.Error("empty family should error")
	}
}
