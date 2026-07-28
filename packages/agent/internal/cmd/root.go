package cmd

import (
	"errors"

	"github.com/spf13/cobra"
)

// Version is overridden at build time via -ldflags.
var Version = "dev"

type rootBuild struct {
	cmd *cobra.Command
	rt  *Runtime
}

// buildRoot constructs the command tree and returns both the cobra root and
// the runtime so Execute() can call ReportError after Execute returns.
func buildRoot() rootBuild {
	flags := &GlobalFlags{}
	rt := NewRuntime(flags)

	root := &cobra.Command{
		Use:   "runtm-api",
		Short: "Runtm (Runtime) Cloud API CLI for AI coding agents",
		Long: `runtm-api - programmatic access to Runtm Cloud (also known as Runtime).

Separate from the pip 'runtm' CLI (which handles local dev, scaffolding, and
deploy). This binary talks only to the hosted cloud API.

Designed to be invoked by AI coding agents (Claude Code, Cursor, etc.) to
create cloud sessions, prompt agents, inspect templates, and check deployment
status. Every command emits structured JSON to stdout and structured errors to
stderr so agents can parse responses without a model loop.

Auth: set RUNTM_API_KEY or run 'runtm login' in the pip CLI.
Docs: https://docs.runtm.com`,
		Version:       Version,
		SilenceErrors: true,
		SilenceUsage:  true,
	}
	root.PersistentFlags().StringVar(&flags.APIURL, "api-url", "", "Override API base URL (also RUNTM_API_URL)")
	root.PersistentFlags().StringVar(&flags.Org, "org", "", "Organization ID for org-scoped operations (also RUNTM_ORG_ID); must match the API key's own org")

	sessionCmd := NewSessionCommand(rt)
	AddSessionExtras(sessionCmd, rt)
	AddSessionDeploy(sessionCmd, rt)
	AddSessionTerminal(sessionCmd, rt)

	root.AddCommand(
		NewAuthCommand(rt),
		sessionCmd,
		NewTemplateCommand(rt),
		NewActivityCommand(rt),
		NewSecretsCommand(rt),
		NewInstructionsCommand(rt),
		NewGuardrailsCommand(rt),
		NewProvidersCommand(rt),
		NewSkillsCommand(rt),
		NewMcpCommand(rt),
		NewToolsCommand(rt),
		NewAgentsCommand(rt),
		NewScheduledAgentsCommand(rt),
	)
	return rootBuild{cmd: root, rt: rt}
}

// NewRootCommand builds the full `runtm` command tree (used for tests / docs).
func NewRootCommand() *cobra.Command {
	return buildRoot().cmd
}

// Execute runs the root command and returns a process exit code. Errors are
// already reported as JSON via the runtime; this just maps them to codes.
func Execute() int {
	build := buildRoot()
	if err := build.cmd.Execute(); err != nil {
		if errors.Is(err, errSilent) {
			return ExitOK
		}
		// `session exec` mirrors the remote command's exit code; its output
		// already went to stdout, so don't print an error envelope.
		var ece *exitCodeError
		if errors.As(err, &ece) {
			return ece.ExitCode()
		}
		return build.rt.ReportError(err)
	}
	return ExitOK
}
