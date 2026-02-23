"""Tests for policy-related types and functions in runtm_shared."""

import pytest

from runtm_shared.types import (
    VALID_TIER_NAMES,
    TenantLimits,
    validate_tier_name,
)


class TestValidateTierName:
    """Tests for validate_tier_name function."""

    def test_valid_tier_names(self) -> None:
        """Valid tier names should be normalized and returned."""
        assert validate_tier_name("starter") == "starter"
        assert validate_tier_name("standard") == "standard"
        assert validate_tier_name("performance") == "performance"
        assert validate_tier_name("pro") == "pro"

    def test_case_insensitive(self) -> None:
        """Tier names should be case-insensitive."""
        assert validate_tier_name("STARTER") == "starter"
        assert validate_tier_name("Standard") == "standard"
        assert validate_tier_name("PERFORMANCE") == "performance"
        assert validate_tier_name("PRO") == "pro"

    def test_whitespace_stripped(self) -> None:
        """Whitespace should be stripped from tier names."""
        assert validate_tier_name("  starter  ") == "starter"
        assert validate_tier_name("\tstandard\n") == "standard"

    def test_invalid_tier_raises(self) -> None:
        """Invalid tier names should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid tier: premium"):
            validate_tier_name("premium")

        with pytest.raises(ValueError, match="Invalid tier: enterprise"):
            validate_tier_name("enterprise")

        with pytest.raises(ValueError, match="Invalid tier:"):
            validate_tier_name("")

    def test_error_message_includes_valid_options(self) -> None:
        """Error message should include valid tier options."""
        with pytest.raises(ValueError) as exc_info:
            validate_tier_name("invalid")

        error_msg = str(exc_info.value)
        assert "starter" in error_msg
        assert "standard" in error_msg
        assert "performance" in error_msg
        assert "pro" in error_msg


class TestValidTierNames:
    """Tests for VALID_TIER_NAMES constant."""

    def test_contains_all_tiers(self) -> None:
        """VALID_TIER_NAMES should contain all tier values."""
        assert "starter" in VALID_TIER_NAMES
        assert "standard" in VALID_TIER_NAMES
        assert "performance" in VALID_TIER_NAMES
        assert "pro" in VALID_TIER_NAMES

    def test_is_frozenset(self) -> None:
        """VALID_TIER_NAMES should be a frozenset (immutable)."""
        assert isinstance(VALID_TIER_NAMES, frozenset)


class TestTenantLimits:
    """Tests for TenantLimits dataclass."""

    def test_default_values_are_none(self) -> None:
        """All limits should default to None (unlimited)."""
        limits = TenantLimits()
        assert limits.max_apps is None
        assert limits.app_lifespan_days is None
        assert limits.deploys_per_hour is None
        assert limits.deploys_per_day is None
        assert limits.concurrent_deploys is None
        assert limits.allowed_tiers is None

    def test_can_set_individual_limits(self) -> None:
        """Individual limits can be set."""
        limits = TenantLimits(
            max_apps=10,
            app_lifespan_days=30,
            deploys_per_hour=20,
            deploys_per_day=100,
            concurrent_deploys=3,
            allowed_tiers=["starter", "standard"],
        )
        assert limits.max_apps == 10
        assert limits.app_lifespan_days == 30
        assert limits.deploys_per_hour == 20
        assert limits.deploys_per_day == 100
        assert limits.concurrent_deploys == 3
        assert limits.allowed_tiers == ["starter", "standard"]

    def test_partial_limits(self) -> None:
        """Some limits can be set while others remain None."""
        limits = TenantLimits(max_apps=5, concurrent_deploys=2)
        assert limits.max_apps == 5
        assert limits.concurrent_deploys == 2
        assert limits.app_lifespan_days is None
        assert limits.deploys_per_hour is None
        assert limits.deploys_per_day is None
        assert limits.allowed_tiers is None
