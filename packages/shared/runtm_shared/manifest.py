"""Pydantic models for runtm.yaml manifest validation."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from runtm_shared.types import MachineTier


class EnvVarType(str, Enum):
    """Supported environment variable types."""

    STRING = "string"
    URL = "url"
    NUMBER = "number"
    BOOLEAN = "boolean"


class PolicyMode(str, Enum):
    """Deployment policy modes."""

    SANDBOX = "sandbox"
    PROD = "prod"


class EnvVar(BaseModel):
    """Environment variable declaration in env_schema.

    Example:
        - name: DATABASE_URL
          type: string
          required: true
          secret: true
          description: "PostgreSQL connection string"
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    type: EnvVarType = EnvVarType.STRING
    required: bool = False
    secret: bool = False  # If true: redact from logs, inject securely
    description: str | None = None
    default: str | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate env var name format (UPPER_SNAKE_CASE)."""
        if not v:
            raise ValueError("env var name cannot be empty")
        if not v.replace("_", "").isalnum():
            raise ValueError("env var name must be alphanumeric with underscores")
        if not v[0].isalpha():
            raise ValueError("env var name must start with a letter")
        return v

    @model_validator(mode="after")
    def validate_secret_no_default(self) -> EnvVar:
        """Secret env vars should not have defaults (security risk)."""
        if self.secret and self.default is not None:
            raise ValueError(
                f"Secret env var '{self.name}' cannot have a default value. "
                "Secrets must be set via 'runtm secrets set' or environment."
            )
        return self


class Connection(BaseModel):
    """Named connection bundle (syntactic sugar over env vars).

    Example:
        - name: supabase
          env_vars: [SUPABASE_URL, SUPABASE_ANON_KEY]
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    env_vars: list[str]

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate connection name format."""
        if not v:
            raise ValueError("connection name cannot be empty")
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError("connection name must be alphanumeric with underscores/hyphens")
        return v.lower()

    @field_validator("env_vars")
    @classmethod
    def validate_env_vars(cls, v: list[str]) -> list[str]:
        """Validate env_vars list is not empty."""
        if not v:
            raise ValueError("connection must reference at least one env var")
        return v


class Policy(BaseModel):
    """Deployment policy configuration.

    In v1, this is informational only. Enforcement comes in org mode.

    Example:
        policy:
          mode: sandbox
          egress: public
          egress_allowlist: []
    """

    model_config = ConfigDict(extra="forbid")

    mode: PolicyMode = PolicyMode.SANDBOX
    egress: str = "public"  # "public" or "allowlist"
    egress_allowlist: list[str] = []

    @field_validator("egress")
    @classmethod
    def validate_egress(cls, v: str) -> str:
        """Validate egress mode."""
        allowed = {"public", "allowlist"}
        if v not in allowed:
            raise ValueError(f"egress must be one of: {', '.join(sorted(allowed))}")
        return v


class Features(BaseModel):
    """Optional features that can be enabled for a deployment.

    Features are declaratively enabled in the manifest, not inferred from env vars.
    This allows agents and humans to explicitly opt-in to features.

    Example:
        features:
          database: true
          auth: true
    """

    model_config = ConfigDict(extra="forbid")

    database: bool = False  # Enable SQLite + persistent volume
    auth: bool = False  # Enable Better Auth (web-app only)


class VolumeMount(BaseModel):
    """Persistent volume mount configuration.

    Volumes persist data across deploys and restarts.
    Auto-created when features.database is enabled.

    Example:
        volumes:
          - name: data
            path: /data
            size_gb: 1
    """

    model_config = ConfigDict(extra="forbid")

    name: str = "data"
    path: str = "/data"
    size_gb: int = Field(default=1, ge=1, le=100)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate volume name format."""
        if not v:
            raise ValueError("volume name cannot be empty")
        if not v.replace("_", "").replace("-", "").isalnum():
            raise ValueError("volume name must be alphanumeric with underscores/hyphens")
        if len(v) > 30:
            raise ValueError("volume name must be 30 characters or less")
        return v.lower()

    @field_validator("path")
    @classmethod
    def validate_path(cls, v: str) -> str:
        """Validate mount path format."""
        if not v.startswith("/"):
            raise ValueError("volume path must be absolute (start with /)")
        if v == "/":
            raise ValueError("volume path cannot be root")
        return v


class Manifest(BaseModel):
    """Runtm manifest schema (runtm.yaml).

    Example:
        name: my-service
        template: backend-service
        runtime: python
        health_path: /health
        port: 8080
        tier: starter
        env_schema:
          - name: DATABASE_URL
            type: string
            required: true
            secret: true
        connections:
          - name: supabase
            env_vars: [SUPABASE_URL, SUPABASE_ANON_KEY]
        policy:
          mode: sandbox
    """

    model_config = ConfigDict(extra="forbid")

    # Required fields
    name: str
    template: str
    runtime: str | None = None  # Optional for docker template

    # Optional fields with defaults
    health_path: str = "/health"
    port: int = 8080
    tier: str = "starter"  # Machine tier: starter, standard, performance

    # Environment variable schema (declares what env vars the app needs)
    env_schema: list[EnvVar] = []

    # Named connection bundles (syntactic sugar over env vars)
    connections: list[Connection] = []

    # Deployment policy (informational in v1, enforced in org mode)
    policy: Policy | None = None

    # Optional features (database, auth)
    features: Features = Field(default_factory=Features)

    # Persistent volumes (auto-populated when features.database=true)
    volumes: list[VolumeMount] = []

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate deployment name format."""
        if not v:
            raise ValueError("name cannot be empty")
        if len(v) > 63:
            raise ValueError("name must be 63 characters or less")
        # Must be lowercase alphanumeric with hyphens
        if not all(c.isalnum() or c == "-" for c in v):
            raise ValueError("name must contain only lowercase letters, numbers, and hyphens")
        if not v[0].isalnum():
            raise ValueError("name must start with a letter or number")
        if not v[-1].isalnum():
            raise ValueError("name must end with a letter or number")
        return v.lower()

    @field_validator("template")
    @classmethod
    def validate_template(cls, v: str) -> str:
        """Validate template name."""
        allowed_templates = {"backend-service", "static-site", "web-app", "docker"}
        if v not in allowed_templates:
            raise ValueError(f"template must be one of: {', '.join(sorted(allowed_templates))}")
        return v

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        """Validate port number."""
        if v < 1 or v > 65535:
            raise ValueError("port must be between 1 and 65535")
        return v

    @field_validator("health_path")
    @classmethod
    def validate_health_path(cls, v: str) -> str:
        """Validate health path format."""
        if not v.startswith("/"):
            raise ValueError("health_path must start with /")
        return v

    @field_validator("tier")
    @classmethod
    def validate_tier(cls, v: str) -> str:
        """Validate machine tier."""
        allowed_tiers = {t.value for t in MachineTier}
        if v not in allowed_tiers:
            raise ValueError(f"tier must be one of: {', '.join(sorted(allowed_tiers))}. Got: {v}")
        return v

    def get_machine_tier(self) -> MachineTier:
        """Get the MachineTier enum for this manifest's tier setting."""
        return MachineTier(self.tier)

    def get_secret_env_vars(self) -> list[EnvVar]:
        """Get all env vars marked as secrets (for log redaction)."""
        return [ev for ev in self.env_schema if ev.secret]

    def get_required_env_vars(self) -> list[EnvVar]:
        """Get all required env vars (for deploy validation)."""
        return [ev for ev in self.env_schema if ev.required]

    def get_connection_env_vars(self, connection_name: str) -> list[str]:
        """Get env var names for a named connection."""
        for conn in self.connections:
            if conn.name == connection_name:
                return conn.env_vars
        return []

    @model_validator(mode="after")
    def validate_connections_reference_schema(self) -> Manifest:
        """Validate that connections reference declared env vars."""
        declared_names = {ev.name for ev in self.env_schema}
        for conn in self.connections:
            for env_var in conn.env_vars:
                if env_var not in declared_names:
                    raise ValueError(
                        f"Connection '{conn.name}' references undeclared env var '{env_var}'. "
                        f"Add it to env_schema first."
                    )
        return self

    @model_validator(mode="after")
    def validate_runtime_for_template(self) -> Manifest:
        """Validate runtime based on template.

        Docker template doesn't require runtime (bring your own Dockerfile).
        All other templates require a valid runtime.
        """
        if self.template == "docker":
            # Runtime is optional/ignored for docker template
            return self

        # Non-docker templates require runtime
        if not self.runtime:
            raise ValueError(
                f"runtime is required for template '{self.template}'. "
                "Use 'python', 'node', or 'fullstack'."
            )

        allowed_runtimes = {"python", "node", "fullstack"}
        if self.runtime not in allowed_runtimes:
            raise ValueError(
                f"runtime must be one of: {', '.join(sorted(allowed_runtimes))}. "
                f"Got: {self.runtime}"
            )
        return self

    @model_validator(mode="after")
    def validate_fullstack_tier(self) -> Manifest:
        """Validate that fullstack apps use adequate resources.

        Fullstack apps run both Node.js and Python simultaneously,
        requiring at least 512MB RAM (standard tier).
        """
        if self.runtime == "fullstack" and self.tier == "starter":
            raise ValueError(
                "Fullstack apps require at least 'standard' tier (512MB RAM). "
                "The 'starter' tier (256MB) is insufficient to run both "
                "Node.js and Python simultaneously. "
                "Update your runtm.yaml: tier: standard"
            )
        return self

    @model_validator(mode="after")
    def validate_features(self) -> Manifest:
        """Validate feature requirements and auto-populate volumes.

        - Auth requires database (Better Auth needs storage)
        - Auth requires AUTH_SECRET env var
        - Auth only works with web-app template
        - Auto-creates volume when database feature is enabled
        """
        # Auth requires database
        if self.features.auth and not self.features.database:
            raise ValueError(
                "features.auth requires features.database to be enabled. "
                "Better Auth needs a database to store users/sessions."
            )

        # Auth only works with web-app template
        if self.features.auth and self.template != "web-app":
            raise ValueError(
                "features.auth is only supported for the 'web-app' template. "
                f"Got template: {self.template}"
            )

        # Auth requires AUTH_SECRET env var
        if self.features.auth:
            has_auth_secret = any(
                ev.name == "AUTH_SECRET" and ev.required for ev in self.env_schema
            )
            if not has_auth_secret:
                raise ValueError(
                    "features.auth requires AUTH_SECRET in env_schema with required=true. "
                    "Add: { name: AUTH_SECRET, type: string, secret: true, required: true }"
                )

        # Auto-add volume when database enabled and no volumes specified
        if self.features.database and not self.volumes:
            # Use object.__setattr__ to modify frozen-like model
            object.__setattr__(
                self,
                "volumes",
                [VolumeMount(name="data", path="/data", size_gb=1)],
            )

        return self

    @classmethod
    def from_yaml(cls, yaml_content: str) -> Manifest:
        """Parse manifest from YAML string.

        Args:
            yaml_content: YAML content as string

        Returns:
            Parsed Manifest object

        Raises:
            ValueError: If YAML is invalid or manifest validation fails
        """
        try:
            data = yaml.safe_load(yaml_content)
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML: {e}") from e

        if not isinstance(data, dict):
            raise ValueError("Manifest must be a YAML dictionary")

        return cls.model_validate(data)

    @classmethod
    def from_file(cls, path: Path) -> Manifest:
        """Parse manifest from file.

        Args:
            path: Path to runtm.yaml file

        Returns:
            Parsed Manifest object

        Raises:
            FileNotFoundError: If file doesn't exist
            ValueError: If YAML is invalid or manifest validation fails
        """
        content = path.read_text()
        return cls.from_yaml(content)

    def to_yaml(self) -> str:
        """Serialize manifest to YAML string.

        Returns:
            YAML representation of the manifest
        """
        data = self.to_dict()
        return yaml.safe_dump(data, default_flow_style=False, sort_keys=False)

    def to_dict(self) -> dict[str, Any]:
        """Convert manifest to dictionary.

        Returns:
            Dictionary representation of the manifest
        """
        data: dict[str, Any] = {
            "name": self.name,
            "template": self.template,
            "health_path": self.health_path,
            "port": self.port,
            "tier": self.tier,
        }

        # Only include runtime if set (docker template doesn't require it)
        if self.runtime:
            data["runtime"] = self.runtime

        # Only include env_schema if non-empty
        if self.env_schema:
            data["env_schema"] = [
                {k: v for k, v in ev.model_dump(mode="json").items() if v is not None}
                for ev in self.env_schema
            ]

        # Only include connections if non-empty
        if self.connections:
            data["connections"] = [conn.model_dump(mode="json") for conn in self.connections]

        # Only include policy if set
        if self.policy:
            data["policy"] = {
                k: v
                for k, v in self.policy.model_dump(mode="json").items()
                if v is not None and v != []
            }

        # Only include features if any are enabled
        if self.features.database or self.features.auth:
            data["features"] = {
                k: v
                for k, v in self.features.model_dump(mode="json").items()
                if v  # Only include enabled features
            }

        # Only include volumes if non-empty
        if self.volumes:
            data["volumes"] = [vol.model_dump(mode="json") for vol in self.volumes]

        return data
