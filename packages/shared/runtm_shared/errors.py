"""Typed error hierarchy with recovery hints for Runtm."""

from __future__ import annotations


class RuntmError(Exception):
    """Base exception for all Runtm errors.

    All errors include a recovery hint to help users fix the issue.
    """

    def __init__(
        self,
        message: str,
        recovery_hint: str | None = None,
        error_code: str | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.recovery_hint = recovery_hint
        self.error_code = error_code

    def __str__(self) -> str:
        parts = [self.message]
        if self.recovery_hint:
            parts.append(f"\nRecovery: {self.recovery_hint}")
        return "".join(parts)

    def to_dict(self) -> dict:
        """Convert error to dictionary for API responses."""
        result = {"error": self.message}
        if self.error_code:
            result["code"] = self.error_code
        if self.recovery_hint:
            result["recovery_hint"] = self.recovery_hint
        return result


# =============================================================================
# Manifest Errors
# =============================================================================


class ManifestError(RuntmError):
    """Base class for manifest-related errors."""

    pass


class ManifestNotFoundError(ManifestError):
    """Raised when runtm.yaml is missing."""

    def __init__(self, path: str = "."):
        super().__init__(
            message=f"Missing runtm.yaml in {path}",
            recovery_hint="Run `runtm init tool python` to create a new project",
            error_code="MANIFEST_NOT_FOUND",
        )


class ManifestValidationError(ManifestError):
    """Raised when runtm.yaml fails validation."""

    def __init__(self, message: str, field: str | None = None):
        hint = "Check your runtm.yaml for syntax errors"
        if field:
            hint = f"Fix the '{field}' field in your runtm.yaml"

        super().__init__(
            message=f"Invalid manifest: {message}",
            recovery_hint=hint,
            error_code="MANIFEST_INVALID",
        )
        self.field = field


class SecretsNotSupportedError(ManifestError):
    """Raised when manifest contains env or secrets (not supported in V0)."""

    def __init__(self, field: str):
        super().__init__(
            message=f"'{field}' is not supported in V0",
            recovery_hint=f"Remove the '{field}' field from your runtm.yaml",
            error_code="SECRETS_NOT_SUPPORTED",
        )


# =============================================================================
# Artifact Errors
# =============================================================================


class ArtifactError(RuntmError):
    """Base class for artifact-related errors."""

    pass


class ArtifactTooLargeError(ArtifactError):
    """Raised when artifact exceeds size limit."""

    def __init__(self, size_bytes: int, max_bytes: int):
        size_mb = size_bytes / (1024 * 1024)
        max_mb = max_bytes / (1024 * 1024)
        super().__init__(
            message=f"Artifact size ({size_mb:.1f} MB) exceeds limit ({max_mb:.0f} MB)",
            recovery_hint="Remove large files, add them to .gitignore, or use .runtmignore",
            error_code="ARTIFACT_TOO_LARGE",
        )


class ArtifactNotFoundError(ArtifactError):
    """Raised when artifact is not found in storage."""

    def __init__(self, key: str):
        super().__init__(
            message=f"Artifact not found: {key}",
            recovery_hint="The artifact may have been deleted. Try deploying again.",
            error_code="ARTIFACT_NOT_FOUND",
        )


class DockerfileNotFoundError(ArtifactError):
    """Raised when Dockerfile is missing."""

    def __init__(self):
        super().__init__(
            message="Missing Dockerfile",
            recovery_hint="Run `runtm init tool python` to create a project with Dockerfile",
            error_code="DOCKERFILE_NOT_FOUND",
        )


# =============================================================================
# Deployment Errors
# =============================================================================


class DeploymentError(RuntmError):
    """Base class for deployment-related errors."""

    pass


class DeploymentNotFoundError(DeploymentError):
    """Raised when deployment is not found."""

    def __init__(self, deployment_id: str):
        super().__init__(
            message=f"Deployment not found: {deployment_id}",
            recovery_hint="Check the deployment ID and try again",
            error_code="DEPLOYMENT_NOT_FOUND",
        )


class DeploymentStateError(DeploymentError):
    """Raised when an invalid state transition is attempted."""

    def __init__(self, from_state: str, to_state: str):
        super().__init__(
            message=f"Cannot transition from '{from_state}' to '{to_state}'",
            recovery_hint="This is likely an internal error. Please report it.",
            error_code="INVALID_STATE_TRANSITION",
        )


class BuildError(DeploymentError):
    """Raised when container build fails."""

    def __init__(self, message: str):
        super().__init__(
            message=f"Build failed: {message}",
            recovery_hint="Check the build logs with `runtm logs <deployment_id>`",
            error_code="BUILD_FAILED",
        )


class BuildTimeoutError(DeploymentError):
    """Raised when build exceeds timeout."""

    def __init__(self, timeout_seconds: int):
        super().__init__(
            message=f"Build timed out after {timeout_seconds} seconds",
            recovery_hint="Simplify your Dockerfile or reduce dependencies",
            error_code="BUILD_TIMEOUT",
        )


class DeployError(DeploymentError):
    """Raised when the deploy step (rollout of an already-built image) fails."""

    def __init__(self, message: str):
        super().__init__(
            message=f"Deploy failed: {message}",
            recovery_hint="Check the deploy logs with `runtm logs <deployment_id>`",
            error_code="DEPLOY_FAILED",
        )


class DeployTimeoutError(DeploymentError):
    """Raised when deployment exceeds timeout."""

    def __init__(self, timeout_seconds: int):
        super().__init__(
            message=f"Deployment timed out after {timeout_seconds} seconds",
            recovery_hint="Check if your app starts within the timeout period",
            error_code="DEPLOY_TIMEOUT",
        )


class HealthCheckError(DeploymentError):
    """Raised when health check fails."""

    def __init__(self, health_path: str, status_code: int | None = None):
        if status_code:
            msg = f"Health check at {health_path} returned {status_code}"
        else:
            msg = f"Health check at {health_path} failed"
        super().__init__(
            message=msg,
            recovery_hint="Ensure your /health endpoint returns 200 and responds quickly",
            error_code="HEALTH_CHECK_FAILED",
        )


# =============================================================================
# Auth Errors
# =============================================================================


class AuthError(RuntmError):
    """Base class for authentication errors."""

    pass


class InvalidTokenError(AuthError):
    """Raised when API token is invalid."""

    def __init__(self):
        super().__init__(
            message="Invalid or missing API key",
            recovery_hint="Set RUNTM_API_KEY environment variable or run `runtm login`",
            error_code="INVALID_TOKEN",
        )


class RateLimitError(AuthError):
    """Raised when rate limit is exceeded."""

    def __init__(self, retry_after_seconds: int | None = None):
        msg = "Rate limit exceeded"
        hint = "Wait before making more requests"
        if retry_after_seconds:
            hint = f"Wait {retry_after_seconds} seconds before retrying"
        super().__init__(
            message=msg,
            recovery_hint=hint,
            error_code="RATE_LIMIT_EXCEEDED",
        )
        self.retry_after_seconds = retry_after_seconds


# =============================================================================
# Provider Errors
# =============================================================================


class ProviderError(RuntmError):
    """Base class for provider-related errors."""

    pass


class FlyError(ProviderError):
    """Raised when Fly.io API returns an error."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(
            message=f"Fly.io error: {message}",
            recovery_hint="Check your FLY_API_TOKEN and try again",
            error_code="FLY_ERROR",
        )
        self.status_code = status_code


class ProviderNotConfiguredError(ProviderError):
    """Raised when provider is not configured."""

    def __init__(self, provider: str):
        super().__init__(
            message=f"Provider '{provider}' is not configured",
            recovery_hint=f"Set the required environment variables for {provider}",
            error_code="PROVIDER_NOT_CONFIGURED",
        )


# =============================================================================
# Storage Errors
# =============================================================================


class StorageError(RuntmError):
    """Base class for storage-related errors."""

    pass


class StorageWriteError(StorageError):
    """Raised when writing to storage fails."""

    def __init__(self, key: str, reason: str):
        super().__init__(
            message=f"Failed to write artifact '{key}': {reason}",
            recovery_hint="Check storage configuration and permissions",
            error_code="STORAGE_WRITE_ERROR",
        )


class StorageReadError(StorageError):
    """Raised when reading from storage fails."""

    def __init__(self, key: str, reason: str):
        super().__init__(
            message=f"Failed to read artifact '{key}': {reason}",
            recovery_hint="The artifact may have been deleted. Try deploying again.",
            error_code="STORAGE_READ_ERROR",
        )
