"""Pluggable policy system for tenant resource limits.

The provider is loaded dynamically from settings.policy_provider.
External packages can implement their own provider (e.g., subscription-based limits).

check_deploy() is the main entry point - it fetches limits internally
to avoid mismatch between get_limits() and check_deploy() calls.

Usage:
    from runtm_api.services.policy import get_policy_provider

    provider = get_policy_provider()
    result = provider.check_deploy(tenant_id, db, requested_tier="performance")

    if not result.allowed:
        raise HTTPException(403, detail=result.reason)

    # Use result.limits.concurrent_deploys for Redis reservation
    # Use result.expires_at when creating deployment record

Custom Provider Example:
    # In runtm_cloud/services/policy.py
    class SubscriptionPolicyProvider:
        def check_deploy(self, tenant_id, db, requested_tier=None):
            subscription = get_subscription(tenant_id)
            limits = get_limits_for_tier(subscription.tier)
            # ... validation logic ...
            return PolicyCheckResult(allowed=True, limits=limits)

    # In environment:
    POLICY_PROVIDER=runtm_cloud.services.policy:SubscriptionPolicyProvider
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import TYPE_CHECKING, Protocol

from runtm_shared.types import TenantLimits, validate_tier_name

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@dataclass
class PolicyCheckResult:
    """Result of a policy check.

    Attributes:
        allowed: Whether the deploy is allowed
        reason: Human-readable reason if denied
        expires_at: Expiry timestamp for new deployments (None = forever)
        limits: The tenant's limits (for concurrent deploy reservation)
    """

    allowed: bool
    reason: str | None = None
    expires_at: datetime | None = None
    limits: TenantLimits | None = None


class PolicyProvider(Protocol):
    """Protocol for policy providers.

    Implement this to create custom policy logic.
    The provider is loaded dynamically from settings.policy_provider.
    """

    def check_deploy(
        self,
        tenant_id: str,
        db: Session,
        requested_tier: str | None = None,
        app_name: str | None = None,
    ) -> PolicyCheckResult:
        """Check if tenant can create a deployment.

        Returns limits in result so caller can use concurrent_deploys
        for atomic reservation without a separate get_limits() call.

        NOTE: This does NOT handle concurrent deploy reservation.
        That is done separately with atomic Redis operations.

        Args:
            tenant_id: Tenant making the request
            db: Database session for queries
            requested_tier: Machine tier being requested (optional)
            app_name: Name of the app being deployed (for redeploy detection)

        Returns:
            PolicyCheckResult with allowed status, reason, expires_at, and limits
        """
        ...


class DefaultPolicyProvider:
    """Default provider that reads limits from Settings.

    All tenants get the same limits (configured via environment variables).
    For per-tenant limits, implement a custom provider.
    """

    def __init__(self) -> None:
        from runtm_api.core.config import get_settings

        self._settings = get_settings()

    def _get_limits(self) -> TenantLimits:
        """Get default limits from settings (all tenants same)."""
        s = self._settings
        return TenantLimits(
            max_apps=s.default_max_apps_per_tenant,
            app_lifespan_days=s.default_app_lifespan_days,
            deploys_per_hour=s.default_deploys_per_hour,
            deploys_per_day=s.default_deploys_per_day,
            concurrent_deploys=s.default_concurrent_deploys,
            allowed_tiers=s.parsed_allowed_tiers,
        )

    def check_deploy(
        self,
        tenant_id: str,
        db: Session,
        requested_tier: str | None = None,
        app_name: str | None = None,
    ) -> PolicyCheckResult:
        """Check limits and return result with limits attached.

        Checks (in order):
        1. Machine tier allowlist
        2. Max apps (is_latest=True, not DESTROYED/FAILED)
        3. Deploy rate (hourly)
        4. Deploy rate (daily)

        NOTE: Concurrent deploy limit is NOT checked here.
        It must be handled with atomic Redis operations in the endpoint.

        Args:
            tenant_id: Tenant making the request
            db: Database session for queries
            requested_tier: Machine tier being requested

        Returns:
            PolicyCheckResult with limits attached for concurrent reservation
        """
        limits = self._get_limits()

        # 1. Check machine tier allowlist
        if limits.allowed_tiers and requested_tier:
            try:
                normalized = validate_tier_name(requested_tier)
                if normalized not in limits.allowed_tiers:
                    return PolicyCheckResult(
                        allowed=False,
                        reason=f"Machine tier '{requested_tier}' not available. "
                        f"Allowed: {', '.join(limits.allowed_tiers)}",
                        limits=limits,
                    )
            except ValueError as e:
                return PolicyCheckResult(allowed=False, reason=str(e), limits=limits)

        # 2. Check max apps (is_latest=True, not DESTROYED/FAILED)
        # Skip this check if deploying an existing app (redeploy)
        if limits.max_apps is not None:
            is_redeploy = app_name and self._app_exists(tenant_id, db, app_name)
            if not is_redeploy:
                app_count = self._count_active_apps(tenant_id, db)
                if app_count >= limits.max_apps:
                    return PolicyCheckResult(
                        allowed=False,
                        reason=f"App limit reached ({app_count}/{limits.max_apps}). "
                        "Destroy an existing app to deploy a new one.",
                        limits=limits,
                    )

        # 3. Check deploy rate (hourly)
        if limits.deploys_per_hour is not None:
            hourly = self._count_deploys_in_window(tenant_id, db, hours=1)
            if hourly >= limits.deploys_per_hour:
                return PolicyCheckResult(
                    allowed=False,
                    reason=f"Hourly deploy limit reached ({limits.deploys_per_hour}/hour).",
                    limits=limits,
                )

        # 4. Check deploy rate (daily)
        if limits.deploys_per_day is not None:
            daily = self._count_deploys_in_window(tenant_id, db, hours=24)
            if daily >= limits.deploys_per_day:
                return PolicyCheckResult(
                    allowed=False,
                    reason=f"Daily deploy limit reached ({limits.deploys_per_day}/day).",
                    limits=limits,
                )

        # Calculate expires_at for new deployment
        expires_at = None
        if limits.app_lifespan_days is not None:
            expires_at = datetime.now(timezone.utc) + timedelta(days=limits.app_lifespan_days)

        return PolicyCheckResult(allowed=True, expires_at=expires_at, limits=limits)

    def _count_active_apps(self, tenant_id: str, db: Session) -> int:
        """Count active apps: is_latest=True and not DESTROYED/FAILED.

        FAILED deploys don't burn slots - user can retry without destroying.
        Only DESTROYED apps explicitly free a slot.

        Args:
            tenant_id: Tenant to count apps for
            db: Database session

        Returns:
            Count of active apps (logical apps, not deployment versions)
        """
        from runtm_api.db.models import Deployment
        from runtm_shared.types import DeploymentState

        return (
            db.query(Deployment)
            .filter(
                Deployment.tenant_id == tenant_id,
                Deployment.is_latest == True,  # noqa: E712
                Deployment.state.notin_([DeploymentState.DESTROYED, DeploymentState.FAILED]),
            )
            .count()
        )

    def _app_exists(self, tenant_id: str, db: Session, app_name: str) -> bool:
        """Check if an app with this name already exists (for redeploy detection).

        Returns True if there's any deployment with this name that is not DESTROYED.
        This allows redeploys of existing apps even when at the app limit.

        Args:
            tenant_id: Tenant to check for
            db: Database session
            app_name: Name of the app to check

        Returns:
            True if app exists and can be redeployed
        """
        from runtm_api.db.models import Deployment
        from runtm_shared.types import DeploymentState

        return (
            db.query(Deployment)
            .filter(
                Deployment.tenant_id == tenant_id,
                Deployment.name == app_name,
                Deployment.state != DeploymentState.DESTROYED,
            )
            .first()
            is not None
        )

    def _count_deploys_in_window(self, tenant_id: str, db: Session, hours: int) -> int:
        """Count deployment requests in the last N hours.

        Counts all deployments created (queued), not just successful ones.
        Uses (tenant_id, created_at) index for efficiency.

        Args:
            tenant_id: Tenant to count deploys for
            db: Database session
            hours: Time window in hours

        Returns:
            Count of deployments created in the window
        """
        from runtm_api.db.models import Deployment

        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        return (
            db.query(Deployment)
            .filter(
                Deployment.tenant_id == tenant_id,
                Deployment.created_at >= since,
            )
            .count()
        )


@lru_cache
def get_policy_provider() -> PolicyProvider:
    """Load and cache the policy provider from settings.

    The provider class is loaded dynamically from settings.policy_provider.
    This allows external packages to inject their own policy logic.

    Returns:
        Cached PolicyProvider instance
    """
    from runtm_api.core.config import get_settings

    settings = get_settings()
    module_path, class_name = settings.policy_provider.rsplit(":", 1)
    module = importlib.import_module(module_path)
    provider_class = getattr(module, class_name)
    return provider_class()


def clear_policy_provider_cache() -> None:
    """Clear cached provider. Call in tests or admin reload."""
    get_policy_provider.cache_clear()
