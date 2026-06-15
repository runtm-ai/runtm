package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/coder/websocket"
	"github.com/runtm-ai/runtm/packages/agent/internal/auth"
	"github.com/runtm-ai/runtm/packages/agent/internal/client"
	"github.com/spf13/cobra"
	"golang.org/x/term"
)

// AddSessionTerminal attaches WebSocket-backed terminal commands to the session
// command tree:
//
//	session connect <id>          interactive PTY (raw passthrough)
//	session exec <id> -- <cmd>    run one command, capture output, exit code
//
// Both leverage the same /api/sessions/{id}/terminal WebSocket the dashboard
// uses. Terminal access is gated by a short-lived signed token minted via
// POST /api/sessions/{id}/ws-token (capability "terminal", scope
// sessions:terminal), so the API key must carry that scope.
func AddSessionTerminal(sessionCmd *cobra.Command, rt *Runtime) {
	sessionCmd.AddCommand(
		newSessionConnect(rt),
		newSessionExec(rt),
	)
}

// --- shared transport -----------------------------------------------------

// mintTerminalToken exchanges the API key for a short-lived (5 min) terminal
// token. The WebSocket endpoint does not accept the API key directly.
func mintTerminalToken(c *client.Client, sessionID string) (string, error) {
	resp, err := c.PostJSON(
		"/sessions/"+url.PathEscape(sessionID)+"/ws-token",
		map[string]any{"capability": "terminal"},
	)
	if err != nil {
		return "", err
	}
	var out struct {
		Token string `json:"token"`
	}
	if jerr := json.Unmarshal(resp, &out); jerr != nil || out.Token == "" {
		return "", fmt.Errorf("could not parse terminal token from ws-token response: %w", jerr)
	}
	return out.Token, nil
}

// terminalWSURL turns the REST API base URL into the ws(s) terminal URL,
// carrying the minted token plus PTY sizing/identity as query params.
func terminalWSURL(apiURL, sessionID, token, terminalID string, cols, rows int) (string, error) {
	u, err := url.Parse(strings.TrimRight(apiURL, "/"))
	if err != nil {
		return "", fmt.Errorf("invalid api url %q: %w", apiURL, err)
	}
	switch u.Scheme {
	case "https":
		u.Scheme = "wss"
	case "http":
		u.Scheme = "ws"
	case "ws", "wss":
		// already a websocket scheme
	default:
		return "", fmt.Errorf("unsupported api url scheme %q (expected http/https)", u.Scheme)
	}
	u.Path = u.Path + "/sessions/" + sessionID + "/terminal"
	q := url.Values{}
	q.Set("token", token)
	q.Set("terminal", terminalID)
	q.Set("user", "cli")
	if cols > 0 {
		q.Set("cols", strconv.Itoa(cols))
	}
	if rows > 0 {
		q.Set("rows", strconv.Itoa(rows))
	}
	u.RawQuery = q.Encode()
	return u.String(), nil
}

// dialTerminal mints a token and opens the terminal WebSocket. The auth headers
// mirror the REST client; the token query param is the real authorizer.
func dialTerminal(ctx context.Context, c *client.Client, creds *auth.Credentials, sessionID, terminalID string, cols, rows int) (*websocket.Conn, error) {
	token, err := mintTerminalToken(c, sessionID)
	if err != nil {
		return nil, err
	}
	wsURL, err := terminalWSURL(creds.APIURL, sessionID, token, terminalID, cols, rows)
	if err != nil {
		return nil, err
	}
	h := http.Header{}
	h.Set("Authorization", "Bearer "+creds.APIKey)
	if creds.OrganizationID != "" {
		h.Set("X-Organization-Id", creds.OrganizationID)
	}
	conn, _, err := websocket.Dial(ctx, wsURL, &websocket.DialOptions{HTTPHeader: h})
	if err != nil {
		return nil, fmt.Errorf("terminal websocket dial failed: %w", err)
	}
	// PTY replay buffers on reconnect can be large; lift the default 32KiB cap.
	conn.SetReadLimit(32 << 20)
	return conn, nil
}

// safeConn serializes writes — coder/websocket forbids concurrent Write calls,
// and `connect` writes input (stdin goroutine) and pong/resize (read loop).
type safeConn struct {
	c  *websocket.Conn
	mu sync.Mutex
}

func (s *safeConn) write(ctx context.Context, typ websocket.MessageType, b []byte) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.c.Write(ctx, typ, b)
}

// controlMessage is the text-frame envelope the terminal server emits.
type controlMessage struct {
	Type    string `json:"type"`
	Message string `json:"message"`
}

// --- connect (interactive PTY) -------------------------------------------

func newSessionConnect(rt *Runtime) *cobra.Command {
	var terminalID string
	cmd := &cobra.Command{
		Use:   "connect <session_id>",
		Short: "Attach an interactive terminal to a session (WebSocket PTY)",
		Long: `Opens a raw, interactive shell against the session's sandbox over the same
WebSocket the dashboard terminal uses. Keystrokes (including Ctrl-C) pass
straight through to the remote PTY. Resize the window and the remote PTY
follows. Exit the remote shell ('exit' or Ctrl-D) to disconnect.

Requires a TTY on stdin. For scripted, non-interactive command execution use
'runtm-api session exec' instead.

Scope required: sessions:terminal.`,
		Args: cobra.ExactArgs(1),
		RunE: func(cmd *cobra.Command, args []string) error {
			sessionID := args[0]
			// Default to a fresh terminal per invocation (like opening a new
			// tab) so reconnecting never replays a dead shell's state. Pass
			// --terminal to attach to a specific one.
			if terminalID == "" {
				terminalID = fmt.Sprintf("cli-%d", time.Now().UnixNano())
			}
			stdinFd := int(os.Stdin.Fd())
			if !term.IsTerminal(stdinFd) {
				return fmt.Errorf("session connect requires an interactive terminal on stdin; use 'session exec' for scripted commands")
			}

			c, creds, err := rt.Client()
			if err != nil {
				return err
			}

			cols, rows, err := term.GetSize(stdinFd)
			if err != nil || cols == 0 {
				cols, rows = 80, 24
			}

			ctx, cancel := context.WithCancel(cmd.Context())
			defer cancel()

			conn, err := dialTerminal(ctx, c, creds, sessionID, terminalID, cols, rows)
			if err != nil {
				return err
			}
			defer conn.Close(websocket.StatusNormalClosure, "client done")
			sc := &safeConn{c: conn}

			// Raw mode so keystrokes/control chars flow untouched to the PTY.
			oldState, err := term.MakeRaw(stdinFd)
			if err != nil {
				return fmt.Errorf("failed to set terminal raw mode: %w", err)
			}
			defer term.Restore(stdinFd, oldState)

			// stdin -> websocket (binary frames).
			go func() {
				buf := make([]byte, 4096)
				for {
					n, readErr := os.Stdin.Read(buf)
					if n > 0 {
						if werr := sc.write(ctx, websocket.MessageBinary, buf[:n]); werr != nil {
							cancel()
							return
						}
					}
					if readErr != nil {
						cancel()
						return
					}
				}
			}()

			// SIGWINCH -> resize control frame.
			winch := make(chan os.Signal, 1)
			signal.Notify(winch, syscall.SIGWINCH)
			defer signal.Stop(winch)
			go func() {
				for {
					select {
					case <-ctx.Done():
						return
					case <-winch:
						nc, nr, gerr := term.GetSize(stdinFd)
						if gerr != nil || nc == 0 {
							continue
						}
						msg, _ := json.Marshal(map[string]any{"type": "resize", "cols": nc, "rows": nr})
						_ = sc.write(ctx, websocket.MessageText, msg)
					}
				}
			}()

			// websocket -> stdout, plus control-frame handling.
			for {
				typ, data, rerr := conn.Read(ctx)
				if rerr != nil {
					// Normal close (remote shell exited) or our own cancel.
					// Restore the terminal before printing so the hint renders
					// in cooked mode, and remind the user the sandbox lives on
					// — disconnecting does not pause it, and idle sandboxes
					// eventually expire.
					term.Restore(stdinFd, oldState)
					fmt.Fprintf(rt.Stderr, "\nDisconnected — the sandbox is still running. Run 'runtm-api session pause %s' to preserve it (idle sandboxes expire).\n", sessionID)
					return nil
				}
				if typ == websocket.MessageBinary {
					os.Stdout.Write(data)
					continue
				}
				var msg controlMessage
				if json.Unmarshal(data, &msg) != nil {
					continue
				}
				switch msg.Type {
				case "ping":
					pong, _ := json.Marshal(map[string]any{"type": "pong", "timestamp": time.Now().Unix()})
					_ = sc.write(ctx, websocket.MessageText, pong)
				case "error":
					term.Restore(stdinFd, oldState)
					fmt.Fprintf(rt.Stderr, "terminal error: %s\n", msg.Message)
					return errSilent
				case "sandbox_expired":
					term.Restore(stdinFd, oldState)
					fmt.Fprintf(rt.Stderr, "sandbox expired: %s\n", msg.Message)
					return errSilent
				}
			}
		},
	}
	cmd.Flags().StringVar(&terminalID, "terminal", "", "Attach to a specific terminal id (default: a fresh terminal each time; use 'default' to share the dashboard terminal)")
	return cmd
}

// --- exec (one command, captured) ----------------------------------------

// Markers are computed by the remote shell at runtime ($$ / $?), so the literal
// command text the PTY echoes back ("__RUNTM_$$_S") can never false-match the
// printed marker ("__RUNTM_<pid>_S"). The start regex captures the live pid.
var execStartRe = regexp.MustCompile(`__RUNTM_(\d+)_S\r?\n`)

// findExecStart looks for the start sentinel. On success it returns the remote
// shell pid and the buffer slice positioned just after the marker (where the
// command's real output begins).
func findExecStart(buf []byte) (pid string, rest []byte, ok bool) {
	m := execStartRe.FindSubmatchIndex(buf)
	if m == nil {
		return "", buf, false
	}
	return string(buf[m[2]:m[3]]), buf[m[1]:], true
}

// findExecEnd looks for the pid-bound end sentinel. On success it returns the
// captured command output (everything before the marker) and the exit code.
func findExecEnd(buf []byte, pid string) (output []byte, code int, ok bool) {
	endRe := regexp.MustCompile(`__RUNTM_` + regexp.QuoteMeta(pid) + `_E(\d+)\r?\n`)
	m := endRe.FindSubmatchIndex(buf)
	if m == nil {
		return nil, 0, false
	}
	code, _ = strconv.Atoi(string(buf[m[2]:m[3]]))
	return buf[:m[0]], code, true
}

func newSessionExec(rt *Runtime) *cobra.Command {
	var timeoutSec int
	cmd := &cobra.Command{
		Use:   "exec <session_id> -- <command> [args...]",
		Short: "Run one command in a session over the terminal WebSocket",
		Long: `Runs a single command inside the session's sandbox using the terminal
WebSocket, streams its output to stdout, and exits with the command's exit
code. A throwaway PTY is used so it never disturbs your interactive terminals.

Put the command after '--' so its flags are not parsed by runtm-api:
  runtm-api session exec <id> -- ls -la /workspace
  runtm-api session exec <id> -- "npm test"

Output is the raw PTY stream and may contain minor terminal formatting. Scope
required: sessions:terminal.`,
		Args: cobra.MinimumNArgs(2),
		RunE: func(cmd *cobra.Command, args []string) error {
			sessionID := args[0]
			cmdLine := strings.TrimSpace(strings.Join(args[1:], " "))
			if cmdLine == "" {
				return fmt.Errorf("no command given; usage: session exec <id> -- <command>")
			}

			c, creds, err := rt.Client()
			if err != nil {
				return err
			}

			ctx := cmd.Context()
			if timeoutSec > 0 {
				var cancel context.CancelFunc
				ctx, cancel = context.WithTimeout(ctx, time.Duration(timeoutSec)*time.Second)
				defer cancel()
			}

			// Unique, throwaway PTY id -> fresh shell, no clobbering UI terminals.
			terminalID := fmt.Sprintf("cli-exec-%d", time.Now().UnixNano())
			conn, err := dialTerminal(ctx, c, creds, sessionID, terminalID, 200, 50)
			if err != nil {
				return err
			}
			defer conn.Close(websocket.StatusNormalClosure, "exec done")
			sc := &safeConn{c: conn}

			// One command line: print start marker, run, print end marker + $?,
			// then exit so the server tears the PTY down. Markers use $$ so the
			// echoed input can't collide with the printed values.
			payload := fmt.Sprintf(
				"printf '__RUNTM_%%d_S\\n' \"$$\"; %s; __rc=$?; printf '__RUNTM_%%d_E%%d\\n' \"$$\" \"$__rc\"; exit\n",
				cmdLine,
			)

			var (
				buf     []byte
				sent    bool
				started bool
				pid     string
			)
			sendOnce := func() error {
				if sent {
					return nil
				}
				sent = true
				return sc.write(ctx, websocket.MessageBinary, []byte(payload))
			}

			for {
				typ, data, rerr := conn.Read(ctx)
				if rerr != nil {
					return fmt.Errorf("connection closed before the command completed: %w", rerr)
				}

				if typ == websocket.MessageText {
					var msg controlMessage
					if json.Unmarshal(data, &msg) != nil {
						continue
					}
					switch msg.Type {
					case "connected":
						if serr := sendOnce(); serr != nil {
							return serr
						}
					case "ping":
						pong, _ := json.Marshal(map[string]any{"type": "pong", "timestamp": time.Now().Unix()})
						_ = sc.write(ctx, websocket.MessageText, pong)
					case "error":
						return fmt.Errorf("terminal error: %s", msg.Message)
					case "sandbox_expired":
						return fmt.Errorf("sandbox expired: %s", msg.Message)
					}
					continue
				}

				// Binary = PTY output. Ensure the command is sent even if no
				// explicit "connected" frame arrived first.
				if serr := sendOnce(); serr != nil {
					return serr
				}
				buf = append(buf, data...)

				if !started {
					// Drop everything up to and including the start marker (the
					// echoed input + shell prompt) so only real output remains.
					pid, buf, started = findExecStart(buf)
				}
				if started {
					if output, code, ok := findExecEnd(buf, pid); ok {
						os.Stdout.Write(stripLeadingNewline(output))
						if code != 0 {
							return &exitCodeError{code: code}
						}
						return nil
					}
				}
			}
		},
	}
	cmd.Flags().IntVar(&timeoutSec, "timeout", 0, "Abort if the command runs longer than N seconds (0 = no timeout)")
	return cmd
}

func stripLeadingNewline(b []byte) []byte {
	if len(b) > 0 && b[0] == '\r' {
		b = b[1:]
	}
	if len(b) > 0 && b[0] == '\n' {
		b = b[1:]
	}
	return b
}

// exitCodeError carries a non-zero remote exit code up to Execute() so the CLI
// process mirrors it without printing a spurious error envelope.
type exitCodeError struct{ code int }

func (e *exitCodeError) Error() string { return fmt.Sprintf("command exited with code %d", e.code) }
func (e *exitCodeError) ExitCode() int { return e.code }
