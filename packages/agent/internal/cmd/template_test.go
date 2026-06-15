package cmd

import "testing"

func TestParseSessionArgs_Shorthand(t *testing.T) {
	args, err := parseSessionArgs([]string{"BRANCH=main", "TOKEN"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(args) != 2 {
		t.Fatalf("want 2 args, got %d", len(args))
	}

	// KEY=DEFAULT -> optional text with default
	a := args[0]
	if a["key"] != "BRANCH" || a["label"] != "BRANCH" || a["type"] != "text" {
		t.Errorf("BRANCH base fields wrong: %#v", a)
	}
	if a["required"] != false {
		t.Errorf("BRANCH should be optional, got required=%v", a["required"])
	}
	if a["default"] != "main" {
		t.Errorf("BRANCH default = %v, want main", a["default"])
	}

	// KEY (no =) -> required, nil default
	b := args[1]
	if b["required"] != true {
		t.Errorf("TOKEN should be required, got %v", b["required"])
	}
	if b["default"] != nil {
		t.Errorf("TOKEN default = %v, want nil", b["default"])
	}
}

func TestParseSessionArgs_JSONSelect(t *testing.T) {
	spec := `{"key":"ENV","type":"select","options":["dev","prod"],"default":"dev","required":true,"label":"Environment","help_text":"Target env"}`
	args, err := parseSessionArgs([]string{spec})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	a := args[0]
	if a["key"] != "ENV" || a["type"] != "select" || a["label"] != "Environment" {
		t.Errorf("select base fields wrong: %#v", a)
	}
	if a["required"] != true {
		t.Errorf("required = %v, want true", a["required"])
	}
	if a["default"] != "dev" {
		t.Errorf("default = %v, want dev", a["default"])
	}
	if a["help_text"] != "Target env" {
		t.Errorf("help_text = %v, want 'Target env'", a["help_text"])
	}
	opts, ok := a["options"].([]string)
	if !ok || len(opts) != 2 || opts[0] != "dev" || opts[1] != "prod" {
		t.Errorf("options = %#v, want [dev prod]", a["options"])
	}
}

func TestParseSessionArgs_JSONBoolean(t *testing.T) {
	args, err := parseSessionArgs([]string{`{"key":"VERBOSE","type":"boolean","default":"false"}`})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	a := args[0]
	if a["type"] != "boolean" || a["default"] != "false" {
		t.Errorf("boolean fields wrong: %#v", a)
	}
	// label defaults to key, options default to empty slice, required defaults false
	if a["label"] != "VERBOSE" || a["required"] != false {
		t.Errorf("boolean defaults wrong: %#v", a)
	}
	if opts, ok := a["options"].([]string); !ok || len(opts) != 0 {
		t.Errorf("options should default to empty slice, got %#v", a["options"])
	}
}

func TestParseSessionArgs_Errors(t *testing.T) {
	cases := map[string]string{
		"empty key shorthand": "=oops",
		"select no options":   `{"key":"ENV","type":"select"}`,
		"bad type":            `{"key":"X","type":"number"}`,
		"malformed json":      `{"key":"X",`,
		"empty json key":      `{"type":"text"}`,
	}
	for name, spec := range cases {
		t.Run(name, func(t *testing.T) {
			if _, err := parseSessionArgs([]string{spec}); err == nil {
				t.Errorf("expected error for %q, got nil", spec)
			}
		})
	}
}

func TestParseSessionArgs_HelpTextOmittedIsNil(t *testing.T) {
	// help_text and default absent -> nil (JSON null), not empty string.
	args, err := parseSessionArgs([]string{`{"key":"X","type":"text"}`})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if args[0]["help_text"] != nil {
		t.Errorf("help_text = %v, want nil", args[0]["help_text"])
	}
	if args[0]["default"] != nil {
		t.Errorf("default = %v, want nil", args[0]["default"])
	}
}
