package cmd

import (
	"encoding/json"
	"fmt"
	"net/http"
	"strings"
	"testing"
)

// runGuardrails executes `guardrails <argv>` against rt.
func runGuardrails(rt *Runtime, argv ...string) error {
	cmd := NewGuardrailsCommand(rt)
	cmd.SetArgs(argv)
	cmd.SilenceUsage, cmd.SilenceErrors = true, true
	return cmd.Execute()
}

func TestRulesCreateNeedsAScope(t *testing.T) {
	calls := 0
	rt, _ := shareRuntime(t, func(w http.ResponseWriter, r *http.Request) { calls++ })
	t.Setenv("RUNTM_ORG_ID", "org_test")
	for _, argv := range [][]string{
		{"rules", "create", "--name", "x", "--kind", "deny", "--pattern", "p"},
		{"network", "create", "--name", "x", "--kind", "host", "--value", "v"},
		{"rules", "create", "--name", "x", "--kind", "deny", "--pattern", "p", "--template", "t", "--org-wide"},
	} {
		err := runGuardrails(rt, argv...)
		if err == nil || !strings.Contains(err.Error(), "--org-wide") {
			t.Errorf("%v: err = %v, want a --template/--org-wide error", argv, err)
		}
	}
	if calls != 0 {
		t.Errorf("made %d API calls; a rule without a scope must never be sent", calls)
	}
}

func TestRulesCreateWithTemplateIsATemplateGuardrail(t *testing.T) {
	var path string
	var body map[string]any
	rt, _ := shareRuntime(t, func(w http.ResponseWriter, r *http.Request) {
		path = r.URL.Path
		_ = json.NewDecoder(r.Body).Decode(&body)
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{}`)
	})
	t.Setenv("RUNTM_ORG_ID", "org_test")
	err := runGuardrails(rt, "rules", "create", "--name", "no-force-push",
		"--kind", "deny", "--pattern", "git push --force*", "--template", "tpl-1")
	if err != nil {
		t.Fatalf("execute: %v", err)
	}

	if !strings.HasSuffix(path, "/org-templates/tpl-1/guardrails") {
		t.Errorf("path = %s, want the template's guardrails", path)
	}
	if body["type"] != "allowlist_rule_v0" || body["name"] != "no-force-push" {
		t.Errorf("body = %v", body)
	}
}

func TestRulesCreateOrgWideIsADirective(t *testing.T) {
	var path string
	rt, _ := shareRuntime(t, func(w http.ResponseWriter, r *http.Request) {
		path = r.URL.Path
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{}`)
	})
	t.Setenv("RUNTM_ORG_ID", "org_test")
	err := runGuardrails(rt, "network", "create", "--name", "stripe",
		"--kind", "host", "--value", "api.stripe.com", "--org-wide")
	if err != nil {
		t.Fatalf("execute: %v", err)
	}

	if !strings.HasSuffix(path, "/agent-directives/") {
		t.Errorf("path = %s, want the org directives", path)
	}
}

func TestOnlyHooksCanBeAttached(t *testing.T) {
	hooks := findSub(t, NewGuardrailsCommand(newTestRuntime()), "hooks")
	for _, verb := range []string{"attach", "detach"} {
		if c := findSub(t, hooks, verb); c.Hidden {
			t.Errorf("hooks %s should be a real, listed command", verb)
		}
	}

	calls := 0
	rt, _ := shareRuntime(t, func(w http.ResponseWriter, r *http.Request) { calls++ })
	t.Setenv("RUNTM_ORG_ID", "org_test")
	for _, family := range []string{"rules", "network"} {
		for _, verb := range []string{"attach", "detach"} {
			if c := findSub(t, findSub(t, NewGuardrailsCommand(rt), family), verb); !c.Hidden {
				t.Errorf("%s %s should be hidden", family, verb)
			}
			err := runGuardrails(rt, family, verb, "r-1", "--template", "tpl-1")
			if err == nil || !strings.Contains(err.Error(), "--template <template_id>") {
				t.Errorf("%s %s: err = %v, want the way to scope a rule", family, verb, err)
			}
		}
	}
	if calls != 0 {
		t.Errorf("made %d API calls", calls)
	}
}
