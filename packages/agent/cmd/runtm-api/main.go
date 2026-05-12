// Command runtm-api is the Runtm Cloud CLI for AI coding agents.
//
// Build:
//
//	go build -o bin/runtm-api ./cmd/runtm-api
//
// Run against a local backend:
//
//	RUNTM_API_URL=http://localhost:8081/api ./bin/runtm-api auth status
package main

import (
	"os"

	"github.com/runtm-ai/runtm/packages/agent/internal/cmd"
)

func main() {
	os.Exit(cmd.Execute())
}
