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
        """Build timeout defaults to 25 minutes (15 killed real customer builds)."""
        assert Limits.BUILD_TIMEOUT_SECONDS == 40 * 60

    def test_deploy_timeout(self) -> None:
        """Deploy timeout should be 10 minutes."""
        assert Limits.DEPLOY_TIMEOUT_SECONDS == 10 * 60

    def test_job_timeout_exceeds_build_plus_deploy(self) -> None:
        """RQ must never kill a job before the build/deploy timeouts fail it cleanly."""
        assert (
            Limits.JOB_TIMEOUT_SECONDS
            > Limits.BUILD_TIMEOUT_SECONDS + Limits.DEPLOY_TIMEOUT_SECONDS
        )
        assert Limits.JOB_TIMEOUT_SECONDS == (
            Limits.BUILD_TIMEOUT_SECONDS
            + Limits.DEPLOY_TIMEOUT_SECONDS
            + Limits.JOB_TIMEOUT_GRACE_SECONDS
        )

    def test_timeouts_honor_env_overrides(self, monkeypatch) -> None:
        """BUILD_TIMEOUT_SECONDS / DEPLOY_TIMEOUT_SECONDS are documented env knobs."""
        import importlib

        import runtm_shared.types as types_module

        monkeypatch.setenv("BUILD_TIMEOUT_SECONDS", "1800")
        monkeypatch.setenv("DEPLOY_TIMEOUT_SECONDS", "300")
        reloaded = importlib.reload(types_module)
        try:
            assert reloaded.Limits.BUILD_TIMEOUT_SECONDS == 1800
            assert reloaded.Limits.DEPLOY_TIMEOUT_SECONDS == 300
            assert (
                reloaded.Limits.JOB_TIMEOUT_SECONDS
                == 1800 + 300 + reloaded.Limits.JOB_TIMEOUT_GRACE_SECONDS
            )
        finally:
            monkeypatch.delenv("BUILD_TIMEOUT_SECONDS")
            monkeypatch.delenv("DEPLOY_TIMEOUT_SECONDS")
            importlib.reload(types_module)

    def test_timeout_env_override_ignores_garbage(self) -> None:
        """A typo in the env must fall back to the default, never crash import."""
        from unittest.mock import patch

        from runtm_shared.types import _env_int

        assert _env_int("RUNTM_TEST_NOPE", 7) == 7
        for bad in ("", "  ", "abc", "0", "-5"):
            with patch.dict("os.environ", {"RUNTM_TEST_NOPE": bad}):
                assert _env_int("RUNTM_TEST_NOPE", 7) == 7
        with patch.dict("os.environ", {"RUNTM_TEST_NOPE": " 42 "}):
            assert _env_int("RUNTM_TEST_NOPE", 7) == 42

    def test_rate_limit(self) -> None:
        """Rate limit should be 10 deployments per hour."""
        assert Limits.MAX_DEPLOYMENTS_PER_HOUR == 10
