//go:build !windows

package cmd

import (
	"os"
	"os/signal"
	"syscall"
)

// notifyResize delivers terminal-resize events (SIGWINCH) on ch.
func notifyResize(ch chan<- os.Signal) {
	signal.Notify(ch, syscall.SIGWINCH)
}
