"""Configuration management for Runtm API."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Ensure .env is loaded from project root before reading settings
from runtm_shared.env import ensure_env_loaded  # noqa: F401
from runtm_shared.types import AuthMode

# Load .env file from project root
ensure_env_loaded()


# Known weak/default secrets that should never be used in production
WEAK_SECRETS = frozenset(
    {
        "dev-token",
        "dev-token-change-in-production",
        "changeme",
        "secret",
        "password",
        "test",
        "example",
    }
)


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        # Don't specify env_file here - we load it via runtm_shared.env
        # This allows the .env to be in the project root, not the current directory
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Database
    database_url: str = "postgresql://runtm:runtm@localhost:5432/runtm"

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Authentication
    auth_mode: AuthMode = AuthMode.SINGLE_TENANT
    api_secret: str = Field(default="", validation_alias="RUNTM_API_SECRET")

    # Token hashing peppers (versioned for rotation)
    # Generate with: python -c "import secrets; print(secrets.token_hex(32))"
    token_pepper_v1: str = ""  # Original pepper (required for multi-tenant)
    token_pepper_v2: str = ""  # For rotation (empty until needed)
    current_pepper_version: int = 1  # Version for new keys

    # Migration window: comma-separated versions to try during rotation
    # e.g., "1,2" during rotation, "2" after migration complete
    pepper_migration_versions: str = ""

    # Admin API key for internal token management routes
    # Required for POST /internal/v0/tokens
    admin_api_key: str = ""

    # Enable internal HTTP routes (disabled by default, use CLI instead)
    enable_internal_routes: bool = False

    # Storage
    artifact_storage_path: str = "/artifacts"
    artifact_storage_backend: str = "local"  # "local" (dev) or "s3" (production/Tigris)

    # S3 / Tigris configuration (only used when artifact_storage_backend = "s3")
    # Fly.io auto-injects these when you run `fly storage create`
    s3_bucket: str = Field(default="", validation_alias="BUCKET_NAME")
    s3_endpoint_url: str = Field(default="", validation_alias="AWS_ENDPOINT_URL_S3")
    s3_region: str = "auto"

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    # SECURITY: Dev-only bypass for authentication
    # When True (AND debug=True AND api_secret is empty), accepts any token.
    # This is DANGEROUS and should NEVER be enabled in production.
    # Requires explicit opt-in to prevent accidental exposure.
    allow_insecure_dev_auth: bool = False

    # SECURITY: Trust X-Tenant-Id header from internal proxies
    # When True (in single-tenant mode), the API will use the X-Tenant-Id header
    # from requests authenticated with the service token (RUNTM_API_SECRET).
    # This enables multi-tenant data isolation when the API is fronted by a
    # trusted internal proxy that validates user auth.
    trust_tenant_header: bool = False

    # Rate limiting
    rate_limit_enabled: bool = True
    rate_limit_requests_per_hour: int = 10

    # Build optimization
    # Remote builder (default True): Builds AND deploys on Fly's infra in one step
    # Set to False to build locally with Docker, then push to Fly registry
    use_remote_builder: bool = True

    # SECURITY: Explicit override for local builds (dev only)
    # When True, allows building with local Docker socket
    # Forbidden in production - use remote builder instead
    allow_local_builds: bool = False

    # Custom domain configuration
    # When set, deployments will get URLs like <app>.runtm.com instead of <app>.fly.dev
    # Requires DNS provider configuration (Cloudflare) to auto-create CNAME records
    runtm_base_domain: str = ""  # e.g., "runtm.com" - empty means use provider URLs

    # DNS Provider Configuration
    # Used to automatically create CNAME records for custom domain URLs
    # e.g., runtm-abc123.runtm.com -> runtm-abc123.fly.dev
    dns_provider: str = ""  # "cloudflare" or empty to disable

    # Cloudflare DNS Configuration
    # Required when dns_provider = "cloudflare"
    # Get from Cloudflare Dashboard: domain > API section (right sidebar)
    cloudflare_api_token: str = ""  # API token with Zone.DNS edit permission
    cloudflare_zone_id: str = ""  # Zone ID for runtm.com

    # Trusted proxy configuration
    # SECURITY: Only trust X-Forwarded-* headers from these IPs
    # Docker default gateway, localhost, common private ranges
    trusted_proxies: str = "127.0.0.1,::1,172.16.0.0/12,10.0.0.0/8"

    # TLS enforcement (production)
    # When True, rejects HTTP requests (only trusts X-Forwarded-Proto from trusted proxies)
    require_tls: bool = True

    # CORS configuration (production only - debug uses localhost allowlist)
    # Comma-separated origins, e.g., "https://app.runtm.com,https://dashboard.runtm.com"
    cors_allowed_origins: str = ""

    # =========================================================================
    # Policy Provider Configuration
    # =========================================================================
    # Dynamic import path for policy provider
    # Format: "module.path:ClassName"
    # Default uses built-in provider that reads from settings below
    # Override to inject custom logic (e.g., subscription-based limits)
    policy_provider: str = "runtm_api.services.policy:DefaultPolicyProvider"

    # Default limits (used by DefaultPolicyProvider)
    # None = unlimited (no restriction)
    # These apply to all tenants when using DefaultPolicyProvider
    default_max_apps_per_tenant: int | None = None
    default_app_lifespan_days: int | None = None
    default_deploys_per_hour: int | None = None
    default_deploys_per_day: int | None = None
    default_concurrent_deploys: int | None = None

    # Comma-separated list of allowed machine tiers (e.g., "starter,standard")
    # None/empty = all tiers allowed
    default_allowed_tiers: str | None = None

    @property
    def is_production(self) -> bool:
        """Check if running in production mode."""
        return not self.debug

    @property
    def dns_enabled(self) -> bool:
        """Check if DNS provider is configured."""
        return bool(self.dns_provider and self.runtm_base_domain)

    @property
    def peppers(self) -> dict[int, str]:
        """Get all configured peppers as version -> value map.

        Used for HMAC-based token hashing with rotation support.
        """
        result = {}
        if self.token_pepper_v1:
            result[1] = self.token_pepper_v1
        if self.token_pepper_v2:
            result[2] = self.token_pepper_v2
        return result

    @property
    def migration_versions(self) -> set[int]:
        """Get pepper versions to try during migration window.

        During pepper rotation, set pepper_migration_versions="1,2"
        to allow verification against both old and new peppers.
        """
        if not self.pepper_migration_versions:
            return set()
        return {int(v.strip()) for v in self.pepper_migration_versions.split(",") if v.strip()}

    @property
    def cors_origins_list(self) -> list[str]:
        """Parse CORS origins safely.

        Returns:
            List of allowed origins, empty if not configured
        """
        if not self.cors_allowed_origins:
            return []
        return [
            origin.strip()
            for origin in self.cors_allowed_origins.split(",")
            if origin.strip()  # Ignore empty entries
        ]

    @property
    def parsed_allowed_tiers(self) -> list[str] | None:
        """Parse and validate allowed machine tiers.

        Returns:
            List of valid tier names, or None if all tiers allowed
        """
        if not self.default_allowed_tiers:
            return None

        from runtm_shared.types import validate_tier_name

        tiers = []
        for t in self.default_allowed_tiers.split(","):
            t = t.strip()
            if t:
                # validate_tier_name raises ValueError if invalid
                tiers.append(validate_tier_name(t))
        return tiers if tiers else None

    @model_validator(mode="after")
    def validate_build_config(self) -> Settings:
        """Prevent local builds in production.

        SECURITY: Local builds require Docker socket access which is
        a container escape vector. Only allow in debug mode.
        """
        if not self.debug and self.allow_local_builds:
            raise ValueError(
                "ALLOW_LOCAL_BUILDS=true is forbidden in production. "
                "Use USE_REMOTE_BUILDER=true instead."
            )
        return self

    @model_validator(mode="after")
    def validate_production_secrets(self) -> Settings:
        """Fail fast if running production with weak secrets.

        SECURITY: Prevents deploying with default/weak API secrets.
        In production (debug=False), requires:
        - api_secret to be set
        - api_secret to not be a known weak/default value
        - api_secret to be at least 32 characters
        """
        if not self.debug:
            if not self.api_secret:
                raise ValueError(
                    "RUNTM_API_SECRET is required in production. "
                    'Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"'
                )
            if self.api_secret.lower() in WEAK_SECRETS:
                raise ValueError(
                    "RUNTM_API_SECRET cannot be a default/weak value in production. "
                    "Generate a secure secret."
                )
            if len(self.api_secret) < 32:
                raise ValueError("RUNTM_API_SECRET must be at least 32 characters in production.")
        return self


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance.

    Uses lru_cache to avoid re-reading environment on every call.
    """
    return Settings()
