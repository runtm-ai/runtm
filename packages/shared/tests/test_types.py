"""Tests for runtm_shared.types."""

from runtm_shared.types import (
    ALLOWED_TRANSITIONS,
    DeploymentState,
    Limits,
    can_transition,
    is_terminal_state,
)


class TestDeploymentState:
    """Tests for DeploymentState enum."""

    def test_all_states_exist(self) -> None:
        """Verify all expected states are defined."""
        assert DeploymentState.QUEUED.value == "queued"
        assert DeploymentState.BUILDING.value == "building"
        assert DeploymentState.DEPLOYING.value == "deploying"
        assert DeploymentState.READY.value == "ready"
        assert DeploymentState.FAILED.value == "failed"

    def test_state_is_string_enum(self) -> None:
        """States should be usable as strings."""
        assert str(DeploymentState.QUEUED) == "DeploymentState.QUEUED"
        assert DeploymentState.QUEUED == "queued"


class TestStateTransitions:
    """Tests for state transition logic."""

    def test_queued_can_transition_to_building(self) -> None:
        """QUEUED can transition to BUILDING."""
        assert can_transition(DeploymentState.QUEUED, DeploymentState.BUILDING)

    def test_queued_can_transition_to_failed(self) -> None:
        """QUEUED can transition to FAILED (validation error)."""
        assert can_transition(DeploymentState.QUEUED, DeploymentState.FAILED)

    def test_queued_cannot_transition_to_ready(self) -> None:
        """QUEUED cannot skip to READY."""
        assert not can_transition(DeploymentState.QUEUED, DeploymentState.READY)

    def test_building_can_transition_to_deploying(self) -> None:
        """BUILDING can transition to DEPLOYING."""
        assert can_transition(DeploymentState.BUILDING, DeploymentState.DEPLOYING)

    def test_building_can_transition_to_failed(self) -> None:
        """BUILDING can transition to FAILED."""
        assert can_transition(DeploymentState.BUILDING, DeploymentState.FAILED)

    def test_deploying_can_transition_to_ready(self) -> None:
        """DEPLOYING can transition to READY."""
        assert can_transition(DeploymentState.DEPLOYING, DeploymentState.READY)

    def test_deploying_can_transition_to_failed(self) -> None:
        """DEPLOYING can transition to FAILED."""
        assert can_transition(DeploymentState.DEPLOYING, DeploymentState.FAILED)

    def test_ready_allows_redeploy_and_destroy(self) -> None:
        """READY can transition to QUEUED (redeploy) or DESTROYED."""
        assert not is_terminal_state(DeploymentState.READY)
        assert can_transition(DeploymentState.READY, DeploymentState.QUEUED)
        assert can_transition(DeploymentState.READY, DeploymentState.DESTROYED)
        assert not can_transition(DeploymentState.READY, DeploymentState.FAILED)

    def test_failed_allows_retry_and_destroy(self) -> None:
        """FAILED can transition to QUEUED (retry) or DESTROYED."""
        assert not is_terminal_state(DeploymentState.FAILED)
        assert can_transition(DeploymentState.FAILED, DeploymentState.QUEUED)
        assert can_transition(DeploymentState.FAILED, DeploymentState.DESTROYED)

    def test_destroyed_is_terminal(self) -> None:
        """DESTROYED is the only terminal state."""
        assert is_terminal_state(DeploymentState.DESTROYED)

    def test_all_states_have_transitions_defined(self) -> None:
        """Every state should have an entry in ALLOWED_TRANSITIONS."""
        for state in DeploymentState:
            assert state in ALLOWED_TRANSITIONS


class TestLimits:
    """Tests for V0 guardrail limits."""

    def test_artifact_size_limit(self) -> None:
        """Artifact size limit should be 20 MB."""
        assert Limits.MAX_ARTIFACT_SIZE_BYTES == 20 * 1024 * 1024

    def test_build_timeout(self) -> None:
        """Build timeout should be 15 minutes."""
        assert Limits.BUILD_TIMEOUT_SECONDS == 15 * 60

    def test_deploy_timeout(self) -> None:
        """Deploy timeout should be 10 minutes."""
        assert Limits.DEPLOY_TIMEOUT_SECONDS == 10 * 60

    def test_rate_limit(self) -> None:
        """Rate limit should be 10 deployments per hour."""
        assert Limits.MAX_DEPLOYMENTS_PER_HOUR == 10
