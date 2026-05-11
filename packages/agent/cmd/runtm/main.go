// Command runtm is the Runtm Cloud CLI for AI coding agents.
//
// Build:
//
//	go build -o bin/runtm ./cmd/runtm
//
// Run against a local backend:
//
//	RUNTM_API_URL=http://localhost:8081 ./bin/runtm auth status
package main

import (
	"os"

	"github.com/runtm-ai/runtm/packages/agent/internal/cmd"
)

func main() {
	os.Exit(cmd.Execute())
}
