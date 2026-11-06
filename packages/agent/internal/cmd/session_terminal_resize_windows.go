//go:build windows

package cmd

import (
	"os"
)

// notifyResize is a no-op on Windows, which has no SIGWINCH. The remote PTY keeps the size it was opened with; live resize propagation is unsupported.
func notifyResize(ch chan<- os.Signal) {
}
