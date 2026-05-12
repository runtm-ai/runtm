#!/usr/bin/env sh
# Runtm CLI installer.
#
# Compiles `runtm` from source via `go install` and installs the SKILL.md
# files for AI coding agents (Claude Code, Cursor, etc.). Requires Go 1.23+.
#
# Usage:
#   curl -fsSL https://runtm.com/install | sh
#
# Environment variables:
#   RUNTM_VERSION       Module version suffix (default: latest)
#   RUNTM_MODULE        Override module path (advanced)
#   RUNTM_SKIP_SKILLS   Set to 1 to skip skill install
#   RUNTM_SKILLS_REF    Branch / tag for skill files (default: main)

set -eu

MODULE="${RUNTM_MODULE:-github.com/runtm-ai/runtm/packages/agent}"
VERSION="${RUNTM_VERSION:-latest}"
SKILLS_REF="${RUNTM_SKILLS_REF:-main}"

err() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

info() { printf '%s\n' "$*"; }

check_go() {
  if ! command -v go >/dev/null 2>&1; then
    cat >&2 <<'EOF'
error: Go 1.23+ is required to install runtm.

Install Go:
  macOS:  brew install go
  Linux:  https://go.dev/doc/install
  Or:     https://go.dev/dl/

Then re-run this installer.

EOF
    exit 1
  fi
}

install_binary() {
  info "Compiling $MODULE/cmd/runtm-api@$VERSION via go install..."
  if ! GOFLAGS="-trimpath" go install "$MODULE/cmd/runtm-api@$VERSION"; then
    err "go install failed. Re-run with 'go install $MODULE/cmd/runtm-api@$VERSION' to inspect."
  fi

  # Resolve install destination so we can tell the user where it landed.
  gobin="$(go env GOBIN 2>/dev/null || true)"
  if [ -z "$gobin" ]; then
    gopath="$(go env GOPATH 2>/dev/null || printf '')"
    if [ -z "$gopath" ]; then
      gobin="$HOME/go/bin"
    else
      gobin="$gopath/bin"
    fi
  fi

  if [ ! -x "$gobin/runtm-api" ]; then
    err "Binary did not land in $gobin/runtm. Check 'go env GOBIN' / 'go env GOPATH'."
  fi
  info "Installed: $gobin/runtm-api"

  case ":$PATH:" in
    *":$gobin:"*) ;;
    *)
      cat <<EOF

NOTE: $gobin is not on your PATH. Add this to your shell rc:
  export PATH="$gobin:\$PATH"
EOF
      ;;
  esac
}

# Install SKILL.md files for whichever AI tools we detect. Multiple targets
# are fine -- customers may have Claude Code + Cursor side by side.
install_skills() {
  if [ "${RUNTM_SKIP_SKILLS:-0}" = "1" ]; then
    info "Skipping skill install (RUNTM_SKIP_SKILLS=1)."
    return
  fi

  src_url_prefix="https://raw.githubusercontent.com/runtm-ai/runtm/$SKILLS_REF/packages/agent/skills"
  installed_any=0

  for target in \
    "$HOME/.claude/skills/runtm" \
    "$HOME/.cursor/skills/runtm"
  do
    parent_dir="$(dirname "$target")"
    if [ ! -d "$parent_dir" ]; then
      continue
    fi

    mkdir -p "$target"
    for skill in SKILL.md runtm-sessions.md runtm-templates.md runtm-debug.md; do
      if ! curl -fsSL "$src_url_prefix/$skill" -o "$target/$skill"; then
        info "warning: failed to fetch $skill"
      fi
    done
    info "Installed skills to $target"
    installed_any=1
  done

  if [ "$installed_any" -eq 0 ]; then
    cat <<'EOF'

NOTE: No AI agent skills directory found (~/.claude or ~/.cursor).
To install skills manually, copy SKILL.md from:
  https://github.com/runtm-ai/runtm/tree/main/packages/agent/skills
into your agent's skills directory.
EOF
  fi
}

print_next_steps() {
  cat <<'EOF'

Done. Next steps:

  1. Set an API key from https://app.runtm.com (Settings > API Keys):
       export RUNTM_API_KEY=runtm_sk_live_...

  2. Verify it works:
       runtm auth status

  3. Tell your AI agent: "Use runtm to launch a session and ..."

Docs: https://docs.runtm.com/cloud-api
EOF
}

main() {
  check_go
  install_binary
  install_skills
  print_next_steps
}

main "$@"
