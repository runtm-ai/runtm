# runtm-shared

Canonical contracts for Runtm: manifest schema, types, errors, and shared utilities.

## Installation

```bash
pip install runtm-shared
```

## Contents

### Core Modules

| Module | Description |
|--------|-------------|
| `types.py` | Deployment state machine, enums, session types, API types |
| `manifest.py` | Pydantic models for `runtm.yaml` validation |
| `errors.py` | Typed error hierarchy with recovery hints |
| `ids.py` | Deterministic deployment ID generation |
| `env.py` | Environment variable handling and validation |

### Session & Sandbox Types

| Module | Description |
|--------|-------------|
| `types.py` | `Session`, `SessionState`, `SessionMode`, `SandboxConfig`, `AgentType` |

### Discovery & Requests

| Module | Description |
|--------|-------------|
| `discovery.py` | Discovery metadata for searchable deployments |
| `requests.py` | Agent request/proposal handling (`runtm.requests.yaml`) |
| `lockfiles.py` | Lockfile validation and drift detection |

### Infrastructure

| Module | Description |
|--------|-------------|
| `storage/base.py` | Abstract storage interface for artifacts |
| `dns/` | DNS provider abstraction (Cloudflare) |
| `redis.py` | Redis connection utilities |
| `urls.py` | URL generation for deployments |
| `deploy_tracking.py` | Deployment tracking and state management |

### Telemetry

| Module | Description |
|--------|-------------|
| `telemetry/` | Anonymous usage telemetry (opt-out available) |

## Usage

### Types and State Machine

```python
from runtm_shared.types import (
    DeploymentState,
    can_transition,
    MachineTier,
    # Session types
    Session,
    SessionState,
    SessionMode,
    SandboxConfig,
    AgentType,
)

# Check if state transition is valid
if can_transition(DeploymentState.BUILDING, DeploymentState.DEPLOYING):
    # proceed with transition
    pass
```

### Manifest Validation

```python
from runtm_shared.manifest import Manifest

# Load and validate runtm.yaml
manifest = Manifest.from_yaml("runtm.yaml")
print(manifest.name, manifest.template, manifest.runtime)
```

### Error Handling

```python
from runtm_shared.errors import (
    RuntmError,
    ManifestValidationError,
    DeploymentError,
    AuthenticationError,
)

try:
    # ... operation
except ManifestValidationError as e:
    print(f"Invalid manifest: {e}")
    print(f"Recovery: {e.recovery_hint}")
```

### ID Generation

```python
from runtm_shared.ids import generate_deployment_id

# Generate deterministic deployment ID
dep_id = generate_deployment_id(project_name="my-api", user_id="usr_123")
# Returns: dep_abc123...
```

### Session Types

```python
from runtm_shared.types import (
    Session,
    SessionState,
    SessionMode,
    AgentType,
    SandboxConfig,
)

# Create a session config
config = SandboxConfig(
    agent=AgentType.CLAUDE_CODE,
    template="backend-service",
)

# Session states
SessionState.RUNNING
SessionState.STOPPED
SessionState.DESTROYED

# Session modes
SessionMode.AUTOPILOT   # Agent controlled via prompts
SessionMode.INTERACTIVE # User controls agent manually
```

### Discovery Metadata

```python
from runtm_shared.discovery import DiscoveryMetadata

# Load discovery metadata
discovery = DiscoveryMetadata.from_yaml("runtm.discovery.yaml")
print(discovery.description)
print(discovery.tags)
print(discovery.capabilities)
```

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Type checking
mypy runtm_shared
```

## Package Structure

```
runtm_shared/
├── __init__.py
├── types.py          # Core types and state machine
├── manifest.py       # Manifest schema (Pydantic)
├── errors.py         # Error hierarchy
├── ids.py            # ID generation
├── env.py            # Environment handling
├── discovery.py      # Discovery metadata
├── requests.py       # Agent requests
├── lockfiles.py      # Lockfile validation
├── urls.py           # URL generation
├── redis.py          # Redis utilities
├── deploy_tracking.py # Deployment tracking
├── storage/
│   └── base.py       # Storage interface
├── dns/
│   ├── base.py       # DNS provider interface
│   └── cloudflare.py # Cloudflare implementation
└── telemetry/
    ├── base.py       # Telemetry base
    ├── service.py    # Telemetry service
    └── ...           # Other telemetry modules
```
