"""Canonical types for Runtm: deployment state machine, enums, and API types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class MachineTier(str, Enum):
    """Machine size tiers for deployments.

    Tiers:
        STARTER: Default tier for most projects (~$15/month)
                 shared-cpu-2x, 2GB RAM
        PERFORMANCE: Heavier workloads and multi-service apps (~$30/month)
                     shared-cpu-4x, 4GB RAM
        PRO: Memory-intensive apps like OpenClaw (~$55/month)
             shared-cpu-4x, 8GB RAM

    All tiers use auto-stop to minimize costs when idle.
    """

    STARTER = "starter"
    PERFORMANCE = "performance"
    PRO = "pro"


@dataclass
class MachineTierSpec:
    """Specification for a machine tier."""

    tier: MachineTier
    memory_mb: int
    cpus: int
    cpu_kind: str
    description: str
    estimated_cost: str  # Monthly estimate when running 24/7


# Tier specifications - all use auto-stop
MACHINE_TIER_SPECS: dict[MachineTier, MachineTierSpec] = {
    MachineTier.STARTER: MachineTierSpec(
        tier=MachineTier.STARTER,
        memory_mb=2048,
        cpus=2,
        cpu_kind="shared",
        description="Starter: 2 shared CPUs, 2GB RAM",
        estimated_cost="~$15/month (with auto-stop, much less)",
    ),
    MachineTier.PERFORMANCE: MachineTierSpec(
        tier=MachineTier.PERFORMANCE,
        memory_mb=4096,
        cpus=4,
        cpu_kind="shared",
        description="Performance: 4 shared CPUs, 4GB RAM",
        estimated_cost="~$30/month (with auto-stop, much less)",
    ),
    MachineTier.PRO: MachineTierSpec(
        tier=MachineTier.PRO,
        memory_mb=8192,
        cpus=4,
        cpu_kind="shared",
        description="Pro: 4 shared CPUs, 8GB RAM",
        estimated_cost="~$55/month (with auto-stop, much less)",
    ),
}


def get_tier_spec(tier: MachineTier) -> MachineTierSpec:
    """Get the specification for a machine tier.

    Args:
        tier: Machine tier

    Returns:
        MachineTierSpec with CPU/memory configuration
    """
    return MACHINE_TIER_SPECS[tier]


class DeploymentState(str, Enum):
    """Deployment lifecycle states.

    State machine:
        [*] --> queued: POST /deployments
        queued --> building: Worker picks up
        queued --> failed: Validation error
        building --> deploying: Image pushed
        building --> failed: Build error
        deploying --> ready: Health check passed
        deploying --> failed: Deploy/health error
        ready --> queued: Redeployment (new version)
        ready --> destroyed: DELETE /deployments/:id
        failed --> destroyed: DELETE /deployments/:id
        ready --> [*]
        failed --> [*]
        destroyed --> [*]
    """

    QUEUED = "queued"
    BUILDING = "building"
    DEPLOYING = "deploying"
    READY = "ready"
    FAILED = "failed"
    DESTROYED = "destroyed"


# Allowed state transitions
ALLOWED_TRANSITIONS: dict[DeploymentState, set[DeploymentState]] = {
    # DEPLOYING allowed from QUEUED for config-only deploys (skip build)
    DeploymentState.QUEUED: {
        DeploymentState.BUILDING,
        DeploymentState.DEPLOYING,
        DeploymentState.FAILED,
        DeploymentState.DESTROYED,
    },
    DeploymentState.BUILDING: {DeploymentState.DEPLOYING, DeploymentState.FAILED},
    DeploymentState.DEPLOYING: {DeploymentState.READY, DeploymentState.FAILED},
    DeploymentState.READY: {
        DeploymentState.QUEUED,
        DeploymentState.DESTROYED,
    },  # QUEUED for redeploy
    DeploymentState.FAILED: {DeploymentState.QUEUED, DeploymentState.DESTROYED},  # QUEUED to retry
    DeploymentState.DESTROYED: set(),  # Terminal state
}


def can_transition(from_state: DeploymentState, to_state: DeploymentState) -> bool:
    """Check if a state transition is allowed.

    Args:
        from_state: Current deployment state
        to_state: Target deployment state

    Returns:
        True if the transition is allowed, False otherwise
    """
    return to_state in ALLOWED_TRANSITIONS.get(from_state, set())


def is_terminal_state(state: DeploymentState) -> bool:
    """Check if a state is terminal (no further transitions allowed).

    Args:
        state: Deployment state to check

    Returns:
        True if the state is terminal, False otherwise
    """
    return len(ALLOWED_TRANSITIONS.get(state, set())) == 0


class LogType(str, Enum):
    """Types of deployment logs."""

    BUILD = "build"
    DEPLOY = "deploy"
    RUNTIME = "runtime"


class AuthMode(str, Enum):
    """Authentication modes."""

    SINGLE_TENANT = "single_tenant"
    MULTI_TENANT = "multi_tenant"


class ApiKeyScope(str, Enum):
    """API key permission scopes.

    Scopes control what operations an API key can perform:
    - READ: List deployments, view logs, check status
    - DEPLOY: Create and update deployments (implies READ)
    - DELETE: Destroy deployments (implies READ)
    - ADMIN: Full access including token management
    """

    READ = "read"
    DEPLOY = "deploy"
    DELETE = "delete"
    ADMIN = "admin"


# Scope hierarchy: higher scopes include lower ones
SCOPE_HIERARCHY: dict[ApiKeyScope, set[ApiKeyScope]] = {
    ApiKeyScope.ADMIN: {ApiKeyScope.READ, ApiKeyScope.DEPLOY, ApiKeyScope.DELETE},
    ApiKeyScope.DEPLOY: {ApiKeyScope.READ},
    ApiKeyScope.DELETE: {ApiKeyScope.READ},
    ApiKeyScope.READ: set(),
}

# Valid scope values for validation
VALID_SCOPES = frozenset(s.value for s in ApiKeyScope)


def validate_scopes(scopes: list[str]) -> list[str]:
    """Validate and normalize scopes on write.

    Args:
        scopes: List of scope strings to validate

    Returns:
        Canonical sorted list of valid scopes

    Raises:
        ValueError: If any scope is invalid
    """
    scope_set = set(scopes)
    invalid = scope_set - VALID_SCOPES
    if invalid:
        raise ValueError(f"Invalid scopes: {invalid}. Valid scopes: {sorted(VALID_SCOPES)}")
    return sorted(scope_set)  # Dedupe and sort for consistency


def has_scope(granted_scopes: set[str], required_scope: ApiKeyScope) -> bool:
    """Check if granted scopes include the required scope.

    Respects scope hierarchy (e.g., ADMIN includes all scopes).

    Args:
        granted_scopes: Set of scope strings the key has
        required_scope: The scope required for the operation

    Returns:
        True if the required scope is granted (directly or via hierarchy)
    """
    # Check direct grant
    if required_scope.value in granted_scopes:
        return True

    # Check if ADMIN is granted (includes everything)
    if ApiKeyScope.ADMIN.value in granted_scopes:
        return True

    # Check hierarchy - if a higher scope is granted that includes this one
    for scope_str in granted_scopes:
        try:
            scope = ApiKeyScope(scope_str)
            if required_scope in SCOPE_HIERARCHY.get(scope, set()):
                return True
        except ValueError:
            continue

    return False


@dataclass
class AuthContext:
    """Authentication context for API requests.

    In single-tenant mode, only token and tenant_id are populated.
    In multi-tenant mode, all fields are populated from the API key.

    Attributes:
        token: The raw bearer token
        tenant_id: Tenant/org identifier for isolation
        principal_id: User or service account identifier
        api_key_id: The API key ID used for this request
        scopes: Set of granted scope strings
    """

    token: str
    tenant_id: str = "default"
    principal_id: str = "default"
    api_key_id: str | None = None
    scopes: set[str] = field(
        default_factory=lambda: {
            ApiKeyScope.READ.value,
            ApiKeyScope.DEPLOY.value,
            ApiKeyScope.DELETE.value,
        }
    )


@dataclass
class VolumeConfig:
    """Configuration for a persistent volume mount.

    Volumes persist data across deploys and machine restarts.
    Used for SQLite databases and other persistent storage.
    """

    name: str
    path: str
    size_gb: int = 1


@dataclass
class MachineConfig:
    """Configuration for deploying a machine to a provider.

    This is passed to the DeployProvider to create a machine.
    All deployments use auto-stop to minimize costs.
    """

    image: str
    memory_mb: int = 256
    cpus: int = 1
    cpu_kind: str = "shared"
    region: str = "iad"
    env: dict[str, str] = field(default_factory=dict)
    health_check_path: str = "/health"
    internal_port: int = 8080
    auto_stop: bool = True  # Always enabled for cost savings
    auto_stop_timeout: str = "5m"  # Stop after 5 minutes of no traffic
    volumes: list[VolumeConfig] = field(default_factory=list)  # Persistent volumes

    @classmethod
    def from_tier(
        cls,
        tier: MachineTier,
        image: str,
        health_check_path: str = "/health",
        internal_port: int = 8080,
        region: str = "iad",
        env: dict[str, str] | None = None,
        volumes: list[VolumeConfig] | None = None,
    ) -> MachineConfig:
        """Create a MachineConfig from a tier specification.

        Args:
            tier: Machine tier (starter, standard, performance)
            image: Docker image to deploy
            health_check_path: Health check endpoint
            internal_port: Internal container port
            region: Deployment region
            env: Environment variables
            volumes: Persistent volume configurations

        Returns:
            MachineConfig with tier-appropriate resources
        """
        spec = get_tier_spec(tier)
        return cls(
            image=image,
            memory_mb=spec.memory_mb,
            cpus=spec.cpus,
            cpu_kind=spec.cpu_kind,
            health_check_path=health_check_path,
            internal_port=internal_port,
            region=region,
            env=env or {},
            auto_stop=True,
            auto_stop_timeout="5m",
            volumes=volumes or [],
        )


@dataclass
class ProviderResource:
    """Resource identifiers returned by a provider after deployment.

    Stored in provider_resources table to map deployment_id to provider-specific IDs.
    """

    app_name: str
    machine_id: str
    region: str
    image_ref: str
    url: str


@dataclass
class DeploymentInfo:
    """Deployment information returned by API.

    Used for GET /v0/deployments/:id response.
    """

    deployment_id: str
    name: str
    state: DeploymentState
    url: str | None = None
    error_message: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class BuildLogEntry:
    """A single build log entry."""

    log_type: LogType
    content: str
    created_at: datetime


# Guardrails - tuned for V0
class Limits:
    """Hard limits for V0 guardrails."""

    # Artifact limits
    MAX_ARTIFACT_SIZE_BYTES: int = 20 * 1024 * 1024  # 20 MB

    # Timeout limits
    BUILD_TIMEOUT_SECONDS: int = 15 * 60  # 15 minutes
    DEPLOY_TIMEOUT_SECONDS: int = 10 * 60  # 10 minutes

    # Resource limits (defaults for starter tier)
    DEFAULT_MEMORY_MB: int = 2048
    DEFAULT_CPUS: int = 2
    DEFAULT_TIER: MachineTier = MachineTier.STARTER

    # Rate limits
    MAX_DEPLOYMENTS_PER_HOUR: int = 10

    # Idempotency key TTL
    IDEMPOTENCY_KEY_TTL_HOURS: int = 24


@dataclass
class ValidationResult:
    """Result of manifest/project validation."""

    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(self, message: str) -> None:
        """Add an error message."""
        self.errors.append(message)
        self.is_valid = False

    def add_warning(self, message: str) -> None:
        """Add a warning message."""
        self.warnings.append(message)


def create_validation_result() -> ValidationResult:
    """Create a new validation result (initially valid)."""
    return ValidationResult(is_valid=True)


@dataclass
class DnsRecord:
    """A DNS record required for custom domain setup."""

    record_type: str  # A, AAAA, CNAME
    name: str  # e.g., "@" or "www"
    value: str  # IP address or hostname


@dataclass
class CustomDomainInfo:
    """Custom domain configuration and status.

    Returned by providers when adding or checking custom domain status.
    """

    hostname: str
    configured: bool = False
    certificate_status: str = "pending"  # pending, issued, error
    dns_records: list[DnsRecord] = field(default_factory=list)
    error: str | None = None
    check_url: str | None = None  # URL to check certificate status


# =============================================================================
# Policy Types
# =============================================================================

# Valid machine tier names for validation
VALID_TIER_NAMES: frozenset[str] = frozenset(t.value for t in MachineTier)


def validate_tier_name(tier: str) -> str:
    """Validate and normalize a machine tier name.

    Args:
        tier: Tier name to validate (e.g., "starter", "STANDARD")

    Returns:
        Normalized lowercase tier name

    Raises:
        ValueError: If tier is not a valid machine tier
    """
    normalized = tier.strip().lower()
    if normalized not in VALID_TIER_NAMES:
        raise ValueError(
            f"Invalid tier: {tier}. Must be one of: {', '.join(sorted(VALID_TIER_NAMES))}"
        )
    return normalized


@dataclass
class TenantLimits:
    """Resource limits for a tenant.

    Used by policy providers to define per-tenant resource constraints.
    None values mean unlimited/no restriction.

    Attributes:
        max_apps: Maximum number of active apps (logical apps, not versions)
        app_lifespan_days: Days until new apps expire (None = forever)
        deploys_per_hour: Maximum deploy requests per hour
        deploys_per_day: Maximum deploy requests per day
        concurrent_deploys: Maximum simultaneous in-progress deploys
        allowed_tiers: List of allowed machine tiers (None = all tiers)
    """

    max_apps: int | None = None
    app_lifespan_days: int | None = None
    deploys_per_hour: int | None = None
    deploys_per_day: int | None = None
    concurrent_deploys: int | None = None
    allowed_tiers: list[str] | None = None  # None = all tiers allowed


# =============================================================================
# Sandbox Types
# =============================================================================


class SandboxState(str, Enum):
    """Sandbox lifecycle states.

    State machine:
        [*] --> creating: session start
        creating --> running: sandbox ready
        running --> stopped: user exits or timeout
        stopped --> running: session attach
        stopped --> destroyed: session destroy
        running --> destroyed: session destroy --force
    """

    CREATING = "creating"
    RUNNING = "running"
    STOPPED = "stopped"
    DESTROYED = "destroyed"


class AgentType(str, Enum):
    """Supported AI coding agents."""

    CLAUDE_CODE = "claude-code"
    CODEX = "codex"
    GEMINI = "gemini"
    CUSTOM = "custom"


# Default network allowlist for sandboxes
DEFAULT_NETWORK_ALLOWLIST: list[str] = [
    # Package registries
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
    "*.npmjs.com",
    # Git hosting
    "github.com",
    "*.github.com",
    "gitlab.com",
    "bitbucket.org",
    # AI APIs
    "api.anthropic.com",
    "api.openai.com",
    # Runtm
    "*.fly.io",
    "*.fly.dev",
    "api.runtm.dev",
]


@dataclass
class NetworkConfig:
    """Network configuration for sandbox.

    Controls which domains the sandbox can access.
    Uses a proxy with domain allowlist (same as Claude Code).
    """

    enabled: bool = True
    allow_domains: list[str] = field(default_factory=lambda: DEFAULT_NETWORK_ALLOWLIST.copy())


@dataclass
class GuardrailsConfig:
    """Guardrails configuration for sandbox.

    sandbox-runtime handles filesystem isolation automatically,
    including mandatory deny paths (.bashrc, .git/hooks, .vscode/).
    These are additional restrictions.
    """

    network: NetworkConfig = field(default_factory=NetworkConfig)
    allow_write_paths: list[str] = field(default_factory=lambda: ["."])
    deny_write_paths: list[str] = field(default_factory=list)
    timeout_minutes: int = 60  # Auto-stop after this


@dataclass
class SandboxConfig:
    """Configuration for creating a sandbox."""

    agent: AgentType = AgentType.CLAUDE_CODE
    template: str | None = None
    guardrails: GuardrailsConfig = field(default_factory=GuardrailsConfig)
    port_mappings: dict[int, int] = field(
        default_factory=lambda: {
            3000: 3000,  # Frontend
            8080: 8080,  # Backend
        }
    )


@dataclass
class Sandbox:
    """A sandbox instance.

    Represents an isolated environment where an AI agent can code.
    Uses OS-level primitives (bubblewrap/seatbelt) via sandbox-runtime.
    """

    id: str
    session_id: str
    config: SandboxConfig
    state: SandboxState
    workspace_path: str
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=None))
    pid: int | None = None  # Process ID when running


class SessionMode(str, Enum):
    """Session operation mode.

    Modes:
        AUTOPILOT: Agent works autonomously via `runtm prompt` commands
        INTERACTIVE: User controls agent directly via shell
    """

    AUTOPILOT = "autopilot"
    INTERACTIVE = "interactive"


class SessionState(str, Enum):
    """Session lifecycle state.

    State machine:
        [*] --> running: session start
        running --> stopped: user exits or timeout
        stopped --> running: session attach
        stopped --> destroyed: session destroy
        running --> destroyed: session destroy --force
    """

    RUNNING = "running"
    STOPPED = "stopped"
    DESTROYED = "destroyed"


@dataclass
class SessionConstraints:
    """Constraints on what the agent can do in a session.

    Used to restrict agent capabilities for security/policy reasons.
    """

    allow_deploy: bool = True  # Can agent run runtm deploy?
    allow_network: bool = True  # Can agent make network requests?
    allow_install: bool = True  # Can agent install packages?


@dataclass
class Session:
    """A coding session with an AI agent.

    Sessions provide a higher-level abstraction over sandboxes,
    tracking agent state, prompts, and conversation history.
    """

    id: str
    name: str | None = None
    mode: SessionMode = SessionMode.AUTOPILOT
    state: SessionState = SessionState.RUNNING
    agent: AgentType = AgentType.CLAUDE_CODE
    sandbox_id: str = ""  # 1:1 with sandbox in MVP
    workspace_path: str = ""
    initial_prompt: str | None = None  # First prompt given to agent
    claude_session_id: str | None = None  # For --continue support
    constraints: SessionConstraints = field(default_factory=SessionConstraints)
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=None))
    updated_at: datetime = field(default_factory=lambda: datetime.now(tz=None))
