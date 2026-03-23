"""Tests for policy provider."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from runtm_api.services.policy import (
    DefaultPolicyProvider,
    PolicyCheckResult,
    clear_policy_provider_cache,
    get_policy_provider,
)
from runtm_shared.types import TenantLimits


class TestPolicyCheckResult:
    """Tests for PolicyCheckResult dataclass."""

    def test_allowed_result(self) -> None:
        """Test creating an allowed result."""
        result = PolicyCheckResult(allowed=True)
        assert result.allowed is True
        assert result.reason is None
        assert result.expires_at is None
        assert result.limits is None

    def test_denied_result(self) -> None:
        """Test creating a denied result with reason."""
        result = PolicyCheckResult(
            allowed=False,
            reason="App limit reached (5/5).",
        )
        assert result.allowed is False
        assert result.reason == "App limit reached (5/5)."

    def test_result_with_limits(self) -> None:
        """Test result includes limits for concurrent reservation."""
        limits = TenantLimits(max_apps=10, concurrent_deploys=3)
        result = PolicyCheckResult(allowed=True, limits=limits)
        assert result.limits is not None
        assert result.limits.max_apps == 10
        assert result.limits.concurrent_deploys == 3


class TestDefaultPolicyProvider:
    """Tests for DefaultPolicyProvider."""

    @pytest.fixture
    def mock_settings(self) -> MagicMock:
        """Create mock settings."""
        settings = MagicMock()
        settings.default_max_apps_per_tenant = None
        settings.default_app_lifespan_days = None
        settings.default_deploys_per_hour = None
        settings.default_deploys_per_day = None
        settings.default_concurrent_deploys = None
        settings.parsed_allowed_tiers = None
        return settings

    @pytest.fixture
    def provider(self, mock_settings: MagicMock) -> DefaultPolicyProvider:
        """Create provider with mocked settings."""
        with patch("runtm_api.core.config.get_settings", return_value=mock_settings):
            return DefaultPolicyProvider()

    @pytest.fixture
    def mock_db(self) -> MagicMock:
        """Create mock database session."""
        return MagicMock()

    def test_no_limits_always_allowed(
        self, provider: DefaultPolicyProvider, mock_db: MagicMock
    ) -> None:
        """When no limits configured, all deploys should be allowed."""
        result = provider.check_deploy("tenant_1", mock_db)
        assert result.allowed is True
        assert result.reason is None
        assert result.expires_at is None

    def test_tier_allowlist_blocks_invalid_tier(
        self, mock_settings: MagicMock, mock_db: MagicMock
    ) -> None:
        """Should block tiers not in allowlist."""
        mock_settings.parsed_allowed_tiers = ["starter", "pro"]

        with patch("runtm_api.core.config.get_settings", return_value=mock_settings):
            provider = DefaultPolicyProvider()
            result = provider.check_deploy("tenant_1", mock_db, requested_tier="performance")

        assert result.allowed is False
        assert "performance" in result.reason
        assert "starter" in result.reason
        assert "pro" in result.reason

    def test_tier_allowlist_allows_valid_tier(
        self, mock_settings: MagicMock, mock_db: MagicMock
    ) -> None:
        """Should allow tiers in allowlist."""
        mock_settings.parsed_allowed_tiers = ["starter", "pro"]

        with patch("runtm_api.core.config.get_settings", return_value=mock_settings):
            provider = DefaultPolicyProvider()
            result = provider.check_deploy("tenant_1", mock_db, requested_tier="pro")

        assert result.allowed is True

    def test_max_apps_blocks_when_at_limit(
        self, mock_settings: MagicMock, mock_db: MagicMock
    ) -> None:
        """Should block new deploys when at app limit."""
        mock_settings.default_max_apps_per_tenant = 5

        # Mock the count query
        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.count.return_value = 5  # At limit

        with patch("runtm_api.core.config.get_settings", return_value=mock_settings):
            provider = DefaultPolicyProvider()
            result = provider.check_deploy("tenant_1", mock_db)

        assert result.allowed is False
        assert "App limit reached" in result.reason
        assert "5/5" in result.reason

    def test_max_apps_allows_when_under_limit(
        self, mock_settings: MagicMock, mock_db: MagicMock
    ) -> None:
        """Should allow deploys when under app limit."""
        mock_settings.default_max_apps_per_tenant = 5

        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.count.return_value = 3  # Under limit

        with patch("runtm_api.core.config.get_settings", return_value=mock_settings):
            provider = DefaultPolicyProvider()
            result = provider.check_deploy("tenant_1", mock_db)

        assert result.allowed is True

    def test_max_apps_allows_redeploy_when_at_limit(
        self, mock_settings: MagicMock, mock_db: MagicMock
    ) -> None:
        """Should allow redeploying existing app even when at app limit."""
        mock_settings.default_max_apps_per_tenant = 5

        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        # first() returns a deployment (app exists), count() would return 5 (at limit)
        mock_query.first.return_value = MagicMock()  # App exists
        mock_query.count.return_value = 5  # At limit

        with patch("runtm_api.core.config.get_settings", return_value=mock_settings):
            provider = DefaultPolicyProvider()
            # Pass app_name to trigger redeploy detection
            result = provider.check_deploy("tenant_1", mock_db, app_name="existing-app")

        assert result.allowed is True
        assert result.reason is None

    def test_max_apps_blocks_new_app_when_at_limit(
        self, mock_settings: MagicMock, mock_db: MagicMock
    ) -> None:
        """Should block new app when at limit (app doesn't exist)."""
        mock_settings.default_max_apps_per_tenant = 5

        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        # first() returns None (app doesn't exist), count() returns 5 (at limit)
        mock_query.first.return_value = None  # New app
        mock_query.count.return_value = 5  # At limit

        with patch("runtm_api.core.config.get_settings", return_value=mock_settings):
            provider = DefaultPolicyProvider()
            result = provider.check_deploy("tenant_1", mock_db, app_name="brand-new-app")

        assert result.allowed is False
        assert "App limit reached" in result.reason
        assert "Destroy an existing app" in result.reason

    def test_hourly_rate_limit_blocks(self, mock_settings: MagicMock, mock_db: MagicMock) -> None:
        """Should block when hourly deploy rate exceeded."""
        mock_settings.default_deploys_per_hour = 10

        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.count.return_value = 10  # At hourly limit

        with patch("runtm_api.core.config.get_settings", return_value=mock_settings):
            provider = DefaultPolicyProvider()
            result = provider.check_deploy("tenant_1", mock_db)

        assert result.allowed is False
        assert "Hourly deploy limit" in result.reason

    def test_daily_rate_limit_blocks(self, mock_settings: MagicMock, mock_db: MagicMock) -> None:
        """Should block when daily deploy rate exceeded."""
        mock_settings.default_deploys_per_day = 50

        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        # Return 5 for hourly (under limit), 50 for daily (at limit)
        mock_query.count.side_effect = [5, 50]

        with patch("runtm_api.core.config.get_settings", return_value=mock_settings):
            mock_settings.default_deploys_per_hour = 10  # Not at hourly limit
            provider = DefaultPolicyProvider()
            result = provider.check_deploy("tenant_1", mock_db)

        assert result.allowed is False
        assert "Daily deploy limit" in result.reason

    def test_expires_at_set_from_lifespan(
        self, mock_settings: MagicMock, mock_db: MagicMock
    ) -> None:
        """Should set expires_at when lifespan configured."""
        mock_settings.default_app_lifespan_days = 7

        with patch("runtm_api.core.config.get_settings", return_value=mock_settings):
            provider = DefaultPolicyProvider()
            result = provider.check_deploy("tenant_1", mock_db)

        assert result.allowed is True
        assert result.expires_at is not None
        # Should be approximately 7 days from now
        expected = datetime.now(timezone.utc) + timedelta(days=7)
        assert abs((result.expires_at - expected).total_seconds()) < 5

    def test_result_includes_limits(self, mock_settings: MagicMock, mock_db: MagicMock) -> None:
        """Result should include limits for concurrent reservation."""
        mock_settings.default_max_apps_per_tenant = 10
        mock_settings.default_concurrent_deploys = 3

        # Mock the count query
        mock_query = MagicMock()
        mock_db.query.return_value = mock_query
        mock_query.filter.return_value = mock_query
        mock_query.count.return_value = 2  # Under limit

        with patch("runtm_api.core.config.get_settings", return_value=mock_settings):
            provider = DefaultPolicyProvider()
            result = provider.check_deploy("tenant_1", mock_db)

        assert result.limits is not None
        assert result.limits.max_apps == 10
        assert result.limits.concurrent_deploys == 3


class TestGetPolicyProvider:
    """Tests for get_policy_provider function."""

    def teardown_method(self) -> None:
        """Clear cache after each test."""
        clear_policy_provider_cache()

    def test_loads_default_provider(self) -> None:
        """Should load DefaultPolicyProvider by default."""
        mock_settings = MagicMock()
        mock_settings.policy_provider = "runtm_api.services.policy:DefaultPolicyProvider"
        mock_settings.default_max_apps_per_tenant = None
        mock_settings.default_app_lifespan_days = None
        mock_settings.default_deploys_per_hour = None
        mock_settings.default_deploys_per_day = None
        mock_settings.default_concurrent_deploys = None
        mock_settings.parsed_allowed_tiers = None

        with patch("runtm_api.core.config.get_settings", return_value=mock_settings):
            provider = get_policy_provider()
            assert isinstance(provider, DefaultPolicyProvider)

    def test_caches_provider(self) -> None:
        """Provider should be cached after first load."""
        mock_settings = MagicMock()
        mock_settings.policy_provider = "runtm_api.services.policy:DefaultPolicyProvider"
        mock_settings.default_max_apps_per_tenant = None
        mock_settings.default_app_lifespan_days = None
        mock_settings.default_deploys_per_hour = None
        mock_settings.default_deploys_per_day = None
        mock_settings.default_concurrent_deploys = None
        mock_settings.parsed_allowed_tiers = None

        with patch("runtm_api.core.config.get_settings", return_value=mock_settings):
            provider1 = get_policy_provider()
            provider2 = get_policy_provider()
            assert provider1 is provider2

    def test_clear_cache(self) -> None:
        """clear_policy_provider_cache should clear the cache."""
        mock_settings = MagicMock()
        mock_settings.policy_provider = "runtm_api.services.policy:DefaultPolicyProvider"
        mock_settings.default_max_apps_per_tenant = None
        mock_settings.default_app_lifespan_days = None
        mock_settings.default_deploys_per_hour = None
        mock_settings.default_deploys_per_day = None
        mock_settings.default_concurrent_deploys = None
        mock_settings.parsed_allowed_tiers = None

        with patch("runtm_api.core.config.get_settings", return_value=mock_settings):
            provider1 = get_policy_provider()
            clear_policy_provider_cache()
            provider2 = get_policy_provider()
            # Should be different instances after cache clear
            assert provider1 is not provider2
