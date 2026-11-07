# runtm-agents

AI coding agent adapters for runtm.

## Overview

This package provides adapters for integrating various AI coding agents with runtm sandboxes:

- **Claude Code**: Anthropic's CLI coding agent (primary)
- **Codex**: OpenAI's code model (future)
- **Gemini**: Google's AI model (future)

## Installation

```bash
pip install runtm-agents
```

Or for development:

```bash
pip install -e packages/agents
```

## Usage

### Basic Usage

```python
from runtm_agents import get_adapter, run_prompt

# Get an adapter
adapter = get_adapter("claude-code")

# Check if installed
if adapter.check_installed():
    print("Claude Code is available")

# Run a prompt
import asyncio
from pathlib import Path


async def main():
    workspace = Path("./my-project")

    async for output in run_prompt(
        workspace=workspace,
        prompt="Create a simple hello world script",
        agent_type="claude-code",
    ):
        print(f"[{output.type}] {output.content}")


asyncio.run(main())
```

### With Sandbox

```python
from runtm_agents import run_prompt_in_sandbox
from runtm_sandbox.providers.local import LocalSandboxProvider

# Create a sandbox
provider = LocalSandboxProvider()
sandbox = provider.create("sbx_123", config)

# Run prompt in sandbox
async for output in run_prompt_in_sandbox(
    sandbox=sandbox,
    prompt="Build a REST API",
):
    print(output.content)
```

## Agent Adapters

### ClaudeCodeAdapter

Wraps the `claude` CLI tool with:
- `--output-format stream-json` for real-time streaming
- `--dangerously-skip-permissions` for autopilot mode
- Session continuation via `--resume`

Output types:
- `text`: Agent's text responses
- `tool_use`: Tool invocations (Write, Edit, Bash, etc.)
- `error`: Error messages
- `result`: Final result with session ID and cost

## Development

```bash
# Run tests
pytest packages/agents/tests

# Type checking
mypy packages/agents

# Linting
ruff check packages/agents
```
