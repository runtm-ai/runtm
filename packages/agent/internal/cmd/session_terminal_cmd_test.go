package cmd

import (
	"net/url"
	"testing"
)

func TestTerminalWSURL(t *testing.T) {
	cases := []struct {
		name       string
		apiURL     string
		wantScheme string
		wantPath   string
	}{
		{"local http", "http://localhost:8081/api", "ws", "/api/sessions/sess-1/terminal"},
		{"prod https proxy", "https://app.runtm.com/api/cloud", "wss", "/api/cloud/sessions/sess-1/terminal"},
		{"trailing slash", "http://localhost:8081/api/", "ws", "/api/sessions/sess-1/terminal"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			raw, err := terminalWSURL(tc.apiURL, "sess-1", "tok123", "cli", 120, 40)
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			u, err := url.Parse(raw)
			if err != nil {
				t.Fatalf("result not a valid url: %v", err)
			}
			if u.Scheme != tc.wantScheme {
				t.Errorf("scheme = %q, want %q", u.Scheme, tc.wantScheme)
			}
			if u.Path != tc.wantPath {
				t.Errorf("path = %q, want %q", u.Path, tc.wantPath)
			}
			q := u.Query()
			if q.Get("token") != "tok123" {
				t.Errorf("token = %q, want tok123", q.Get("token"))
			}
			if q.Get("cols") != "120" || q.Get("rows") != "40" {
				t.Errorf("size = %sx%s, want 120x40", q.Get("cols"), q.Get("rows"))
			}
			if q.Get("terminal") != "cli" {
				t.Errorf("terminal = %q, want cli", q.Get("terminal"))
			}
		})
	}
}

func TestTerminalWSURL_BadScheme(t *testing.T) {
	if _, err := terminalWSURL("ftp://nope/api", "s", "t", "cli", 80, 24); err == nil {
		t.Fatal("expected error for unsupported scheme")
	}
}

func TestFindExecStart(t *testing.T) {
	// The PTY echoes the literal command (with $$), then the shell prints the
	// resolved marker with the real pid. We must lock onto the printed one.
	buf := []byte("printf '__RUNTM_%d_S\\n' \"$$\"; ls\r\n__RUNTM_4242_S\r\nfile-a\r\n")
	pid, rest, ok := findExecStart(buf)
	if !ok {
		t.Fatal("expected start marker to be found")
	}
	if pid != "4242" {
		t.Errorf("pid = %q, want 4242", pid)
	}
	if got := string(rest); got != "file-a\r\n" {
		t.Errorf("rest = %q, want %q", got, "file-a\r\n")
	}
}

func TestFindExecStart_NotYetPresent(t *testing.T) {
	// Only the echoed command (with literal $$) has arrived — no real marker.
	buf := []byte("printf '__RUNTM_%d_S\\n' \"$$\"; ls\r\n")
	if _, _, ok := findExecStart(buf); ok {
		t.Fatal("must not match the echoed command line containing $$")
	}
}

func TestFindExecEnd(t *testing.T) {
	buf := []byte("hello world\r\n__RUNTM_4242_E0\r\n")
	out, code, ok := findExecEnd(buf, "4242")
	if !ok {
		t.Fatal("expected end marker to be found")
	}
	if code != 0 {
		t.Errorf("code = %d, want 0", code)
	}
	if got := string(out); got != "hello world\r\n" {
		t.Errorf("output = %q, want %q", got, "hello world\r\n")
	}
}

func TestFindExecEnd_NonZeroCode(t *testing.T) {
	buf := []byte("boom\r\n__RUNTM_77_E13\r\n")
	out, code, ok := findExecEnd(buf, "77")
	if !ok {
		t.Fatal("expected end marker")
	}
	if code != 13 {
		t.Errorf("code = %d, want 13", code)
	}
	if string(out) != "boom\r\n" {
		t.Errorf("output = %q, want boom", string(out))
	}
}

func TestFindExecEnd_WrongPidIgnored(t *testing.T) {
	// An end marker for a different pid (shouldn't happen, but be strict).
	buf := []byte("out\r\n__RUNTM_999_E0\r\n")
	if _, _, ok := findExecEnd(buf, "4242"); ok {
		t.Fatal("must not match end marker for a different pid")
	}
}

func TestStripLeadingNewline(t *testing.T) {
	cases := map[string]string{
		"\nhello":   "hello",
		"\r\nhello": "hello",
		"hello":     "hello",
		"":          "",
	}
	for in, want := range cases {
		if got := string(stripLeadingNewline([]byte(in))); got != want {
			t.Errorf("stripLeadingNewline(%q) = %q, want %q", in, got, want)
		}
	}
}
