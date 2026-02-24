"""HTTP client for Runtm API."""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from runtm_shared import generate_idempotency_key
from runtm_shared.errors import (
    DeploymentNotFoundError,
    InvalidTokenError,
    RateLimitError,
    RuntmError,
)

from .config import get_api_url, get_token


@dataclass
class DeploymentInfo:
    """Deployment information from API."""

    deployment_id: str
    name: str
    state: str
    url: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    version: int = 1
    is_latest: bool = True
    previous_deployment_id: str | None = None
    app_name: str | None = None  # Fly.io app name (for domain commands)
    src_hash: str | None = None  # Source hash for config-only validation


@dataclass
class LogEntry:
    """Log entry from API."""

    log_type: str
    content: str
    created_at: datetime


@dataclass
class LogsResponse:
    """Logs response from API."""

    deployment_id: str
    logs: list[LogEntry]
    source: str
    instructions: str | None = None


@dataclass
class DestroyResponse:
    """Destroy response from API."""

    deployment_id: str
    status: str
    message: str


@dataclass
class DeploymentsListResponse:
    """List deployments response from API."""

    deployments: list[DeploymentInfo]
    total: int


@dataclass
class DnsRecord:
    """DNS record for custom domain setup."""

    record_type: str
    name: str
    value: str


@dataclass
class CustomDomainInfo:
    """Custom domain configuration and status."""

    hostname: str
    configured: bool
    certificate_status: str
    dns_records: list[DnsRecord]
    error: str | None = None
    check_url: str | None = None


@dataclass
class SearchResult:
    """Search result from API."""

    deployment_id: str
    name: str
    state: str
    url: str | None
    template: str | None
    runtime: str | None
    version: int
    is_latest: bool
    created_at: datetime
    updated_at: datetime
    # Discovery metadata
    summary: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    capabilities: list[str] | None = None
    use_cases: list[str] | None = None
    match_score: float = 0.0


@dataclass
class SearchResponse:
    """Search response from API."""

    results: list[SearchResult]
    total: int
    query: str


@dataclass
class CloudKeyVerifyResponse:
    """Response from Cloud Backend API key verification."""

    valid: bool
    key_id: str
    name: str
    scopes: list[str]
    expires_at: str | None
    user_id: str


def verify_cloud_api_key(token: str, api_url: str | None = None) -> CloudKeyVerifyResponse:
    """Verify an API key against the API.

    DEPRECATED: The login command now validates via /v1/me directly.
    This function is kept for backward compatibility.

    Args:
        token: The API key to verify (runtm_sk_...)
        api_url: API URL (defaults to config)

    Returns:
        CloudKeyVerifyResponse with key metadata if valid

    Raises:
        InvalidTokenError: If the key is invalid, expired, or revoked
        RuntmError: For other errors
    """
    url = api_url or get_api_url()

    try:
        # Try /v1/me endpoint first (new API)
        response = httpx.get(
            f"{url}/v1/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )

        if response.status_code == 200:
            data = response.json()
            return CloudKeyVerifyResponse(
                valid=True,
                key_id=data.get("api_key_id", "unknown"),
                name=data.get("api_key_name", "API Key"),
                scopes=data.get("scopes", []),
                expires_at=None,
                user_id=data.get("principal_id", "unknown"),
            )
        elif response.status_code == 401:
            raise RuntmError(
                "Invalid API key",
                recovery_hint="Create a new key at https://app.runtm.com/api-keys",
            )
        elif response.status_code == 404:
            # /v1/me doesn't exist, assume key is valid
            return CloudKeyVerifyResponse(
                valid=True,
                key_id="unknown",
                name="API Key",
                scopes=[],
                expires_at=None,
                user_id="unknown",
            )
        else:
            raise RuntmError(f"Key verification failed: {response.status_code}")

    except httpx.RequestError as e:
        raise RuntmError(
            f"Could not connect to API: {e}",
            recovery_hint="Check your internet connection and try again.",
        )


class APIClient:
    """HTTP client for Runtm API.

    Handles authentication, error mapping, and request/response serialization.
    """

    def __init__(
        self,
        api_url: str | None = None,
        token: str | None = None,
        timeout: float = 60.0,
    ):
        """Initialize API client.

        Args:
            api_url: API base URL (defaults to config)
            token: API token (defaults to config)
            timeout: Request timeout in seconds
        """
        self.api_url = api_url or get_api_url()
        self.token = token or get_token()
        self.timeout = timeout

    def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        """Build request headers.

        Args:
            idempotency_key: Optional idempotency key

        Returns:
            Headers dict
        """
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        # Add trace context for end-to-end tracing
        try:
            from runtm_cli.telemetry import get_traceparent

            traceparent = get_traceparent()
            if traceparent:
                headers["traceparent"] = traceparent
        except ImportError:
            pass  # Telemetry not available

        return headers

    def _handle_error(self, response: httpx.Response) -> None:
        """Handle API error response.

        Args:
            response: HTTP response

        Raises:
            Appropriate RuntmError subclass
        """
        if response.status_code == 401:
            raise InvalidTokenError()
        elif response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            raise RateLimitError(retry_after_seconds=int(retry_after) if retry_after else None)
        elif response.status_code == 404:
            # Try to extract deployment ID from URL
            path = str(response.url.path)
            if "/deployments/" in path:
                deployment_id = path.split("/deployments/")[-1].split("/")[0]
                raise DeploymentNotFoundError(deployment_id)
            raise RuntmError("Resource not found")
        elif response.status_code >= 400:
            # Try to parse JSON error response
            try:
                detail = response.json()
                # FastAPI may wrap errors in "detail" key, or return dict directly
                if isinstance(detail, dict):
                    # Check for direct error fields first
                    error_msg = detail.get("error") or detail.get("detail")
                    recovery = detail.get("recovery_hint")
                    # Handle FastAPI validation errors (422 format)
                    if "detail" in detail and isinstance(detail["detail"], list):
                        # FastAPI validation errors are a list of error objects
                        errors = []
                        for err in detail["detail"]:
                            if isinstance(err, dict):
                                loc = err.get("loc", [])
                                msg = err.get("msg", "")
                                field = " -> ".join(str(x) for x in loc if x != "body")
                                errors.append(f"{field}: {msg}" if field else msg)
                            else:
                                errors.append(str(err))
                        error_msg = "Validation error: " + "; ".join(errors)
                        recovery = "Check your request parameters and try again"
                    # If we have a detail key but no error, use detail as the message
                    elif not error_msg and "detail" in detail:
                        error_msg = detail["detail"]
                    # Fallback to string representation
                    if not error_msg:
                        error_msg = str(detail)
                else:
                    error_msg = str(detail)
                    recovery = None
                raise RuntmError(error_msg, recovery_hint=recovery)
            except (ValueError, TypeError):
                # JSON parsing failed, try to read text response
                try:
                    text = response.text
                    if text:
                        raise RuntmError(
                            f"Request failed: {response.status_code}",
                            recovery_hint=f"Server response: {text[:200]}",
                        )
                except Exception:
                    pass
                # Fallback to generic error
                raise RuntmError(
                    f"Request failed: {response.status_code}",
                    recovery_hint="Check your request format and try again. If the problem persists, check the API logs.",
                )

    def check_auth(self) -> bool:
        """Check if authentication is configured and valid.

        Makes a lightweight API call to verify the token is valid,
        not just that it exists. This prevents doing expensive work
        (validation, artifact creation) only to fail on auth later.

        Returns:
            True if authenticated and token is valid
        """
        if not self.token:
            return False

        try:
            # Use /v1/me which requires valid authentication
            # Falls back to /health if /v1/me doesn't exist (older API versions)
            response = httpx.get(
                f"{self.api_url}/v1/me",
                headers=self._headers(),
                timeout=10.0,
            )
            if response.status_code == 200:
                return True
            elif response.status_code == 401:
                return False  # Token is invalid/expired
            elif response.status_code == 404:
                # /v1/me doesn't exist, fall back to checking connectivity
                # (older API versions - assume token is valid if we can connect)
                health_response = httpx.get(
                    f"{self.api_url}/health",
                    headers=self._headers(),
                    timeout=10.0,
                )
                return health_response.status_code == 200
            else:
                return False
        except Exception:
            return False

    def create_deployment(
        self,
        manifest_path: Path,
        artifact_path: Path,
        idempotency_key: str | None = None,
        force_new: bool = False,
        tier: str | None = None,
        secrets: dict[str, str] | None = None,
        src_hash: str | None = None,
        config_only: bool = False,
    ) -> DeploymentInfo:
        """Create a new deployment or redeploy an existing one.

        By default, if a deployment with the same name already exists,
        this will redeploy (update) the existing deployment.

        Args:
            manifest_path: Path to runtm.yaml
            artifact_path: Path to artifact.zip
            idempotency_key: Optional idempotency key (generated if not provided)
            force_new: Force creation of new deployment instead of redeploying
            tier: Optional machine tier override (starter, standard, performance)
            secrets: Optional secrets to inject (passed through to provider, never stored)
            src_hash: Source hash for tracking and config-only validation
            config_only: Skip Docker build and reuse previous image (for config-only changes)

        Returns:
            DeploymentInfo with status

        Security note:
            Secrets are passed in memory via HTTPS and forwarded directly to the
            deployment provider (e.g., Fly.io). They are NEVER stored in the Runtm
            database. Only secret NAMES are logged for debugging purposes.
        """
        import json

        if idempotency_key is None:
            idempotency_key = generate_idempotency_key()

        with open(manifest_path, "rb") as mf, open(artifact_path, "rb") as af:
            files = {
                "manifest": ("runtm.yaml", mf, "text/yaml"),
                "artifact": ("artifact.zip", af, "application/zip"),
            }

            # Build URL with query parameters
            url = f"{self.api_url}/v0/deployments"
            params = []
            if force_new:
                params.append("new=true")
            if tier:
                params.append(f"tier={tier}")
            if config_only:
                params.append("config_only=true")
            if params:
                url += "?" + "&".join(params)

            # Add secrets and metadata as form data (not logged, passed through to worker)
            data = {}
            if secrets:
                # Pass as JSON string in form data
                data["secrets"] = json.dumps(secrets)
            if src_hash:
                data["src_hash"] = src_hash

            response = httpx.post(
                url,
                files=files,
                data=data,
                headers=self._headers(idempotency_key),
                timeout=self.timeout,
            )

        if response.status_code >= 400:
            self._handle_error(response)

        data = response.json()
        return self._parse_deployment(data)

    def get_deployment(self, deployment_id: str) -> DeploymentInfo:
        """Get deployment status.

        Args:
            deployment_id: Deployment ID

        Returns:
            DeploymentInfo
        """
        response = httpx.get(
            f"{self.api_url}/v0/deployments/{deployment_id}",
            headers=self._headers(),
            timeout=self.timeout,
        )

        if response.status_code >= 400:
            self._handle_error(response)

        data = response.json()
        return self._parse_deployment(data)

    def get_logs(
        self,
        deployment_id: str,
        log_type: str | None = None,
        lines: int = 20,
        search: str | None = None,
    ) -> LogsResponse:
        """Get deployment logs.

        Args:
            deployment_id: Deployment ID
            log_type: Optional log type filter (build, deploy, runtime)
            lines: Number of runtime log lines to include (default: 20)
            search: Optional text to filter logs (case-insensitive)

        Returns:
            LogsResponse
        """
        params: dict[str, Any] = {"lines": lines}
        if log_type:
            params["type"] = log_type
        if search:
            params["search"] = search

        response = httpx.get(
            f"{self.api_url}/v0/deployments/{deployment_id}/logs",
            params=params,
            headers=self._headers(),
            timeout=60.0,  # Longer timeout for runtime logs
        )

        if response.status_code >= 400:
            self._handle_error(response)

        data = response.json()
        return LogsResponse(
            deployment_id=data["deployment_id"],
            logs=[
                LogEntry(
                    log_type=entry["log_type"],
                    content=entry["content"],
                    created_at=datetime.fromisoformat(entry["created_at"].replace("Z", "+00:00")),
                )
                for entry in data["logs"]
            ],
            source=data["source"],
            instructions=data.get("instructions"),
        )

    def list_deployments(
        self,
        state: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> DeploymentsListResponse:
        """List all deployments.

        Args:
            state: Optional state filter (queued, building, deploying, ready, failed, destroyed)
            limit: Maximum number of results (default: 50)
            offset: Pagination offset (default: 0)

        Returns:
            DeploymentsListResponse
        """
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if state:
            params["state"] = state

        response = httpx.get(
            f"{self.api_url}/v0/deployments",
            params=params,
            headers=self._headers(),
            timeout=self.timeout,
        )

        if response.status_code >= 400:
            self._handle_error(response)

        data = response.json()
        return DeploymentsListResponse(
            deployments=[self._parse_deployment(dep) for dep in data["deployments"]],
            total=data["total"],
        )

    def search_deployments(
        self,
        query: str,
        state: str | None = None,
        template: str | None = None,
        limit: int = 20,
    ) -> SearchResponse:
        """Search deployments by discovery metadata.

        Args:
            query: Search query (searches description, summary, tags, etc.)
            state: Optional state filter
            template: Optional template filter
            limit: Maximum number of results (default: 20)

        Returns:
            SearchResponse with matching deployments
        """
        params: dict[str, Any] = {"q": query, "limit": limit}
        if state:
            params["state"] = state
        if template:
            params["template"] = template

        response = httpx.get(
            f"{self.api_url}/v0/deployments/search",
            params=params,
            headers=self._headers(),
            timeout=self.timeout,
        )

        if response.status_code >= 400:
            self._handle_error(response)

        data = response.json()
        return SearchResponse(
            results=[self._parse_search_result(r) for r in data["results"]],
            total=data["total"],
            query=data["query"],
        )

    def _parse_search_result(self, data: dict[str, Any]) -> SearchResult:
        """Parse search result from API response.

        Args:
            data: Response data

        Returns:
            SearchResult
        """
        # Extract discovery metadata
        discovery = data.get("discovery") or {}

        return SearchResult(
            deployment_id=data["deployment_id"],
            name=data["name"],
            state=data["state"],
            url=data.get("url"),
            template=data.get("template"),
            runtime=data.get("runtime"),
            version=data.get("version", 1),
            is_latest=data.get("is_latest", True),
            created_at=datetime.fromisoformat(data["created_at"].replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00")),
            summary=discovery.get("summary"),
            description=discovery.get("description"),
            tags=discovery.get("tags"),
            capabilities=discovery.get("capabilities"),
            use_cases=discovery.get("use_cases"),
            match_score=data.get("match_score", 0.0),
        )

    def destroy_deployment(self, deployment_id: str) -> DestroyResponse:
        """Destroy a deployment.

        Args:
            deployment_id: Deployment ID

        Returns:
            DestroyResponse
        """
        response = httpx.delete(
            f"{self.api_url}/v0/deployments/{deployment_id}",
            headers=self._headers(),
            timeout=self.timeout,
        )

        if response.status_code >= 400:
            self._handle_error(response)

        data = response.json()
        return DestroyResponse(
            deployment_id=data["deployment_id"],
            status=data["status"],
            message=data["message"],
        )

    def _parse_deployment(self, data: dict[str, Any]) -> DeploymentInfo:
        """Parse deployment response.

        Args:
            data: Response data

        Returns:
            DeploymentInfo
        """
        return DeploymentInfo(
            deployment_id=data["deployment_id"],
            name=data["name"],
            state=data["state"],
            url=data.get("url"),
            error_message=data.get("error_message"),
            created_at=datetime.fromisoformat(data["created_at"].replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00")),
            version=data.get("version", 1),
            is_latest=data.get("is_latest", True),
            previous_deployment_id=data.get("previous_deployment_id"),
            app_name=data.get("app_name"),
            src_hash=data.get("src_hash"),
        )

    def get_latest_deployment_for_name(self, name: str) -> DeploymentInfo | None:
        """Get the latest deployment for a given project name.

        Used to validate --config-only deploys by checking previous src_hash.

        Args:
            name: Project name from runtm.yaml

        Returns:
            DeploymentInfo if found, None otherwise
        """
        try:
            response = httpx.get(
                f"{self.api_url}/v0/deployments",
                params={"name": name, "limit": 1},
                headers=self._headers(),
                timeout=self.timeout,
            )

            if response.status_code >= 400:
                return None

            data = response.json()
            deployments = data.get("deployments", [])
            if deployments:
                return self._parse_deployment(deployments[0])
            return None
        except Exception:
            return None

    def add_custom_domain(
        self,
        deployment_id: str,
        hostname: str,
    ) -> CustomDomainInfo:
        """Add a custom domain to a deployment.

        Args:
            deployment_id: Deployment ID
            hostname: Custom domain (e.g., "api.example.com")

        Returns:
            CustomDomainInfo with DNS records and status
        """
        response = httpx.post(
            f"{self.api_url}/v0/deployments/{deployment_id}/domains",
            json={"hostname": hostname},
            headers=self._headers(),
            timeout=30.0,
        )

        if response.status_code >= 400:
            self._handle_error(response)

        return self._parse_domain_info(response.json())

    def get_custom_domain_status(
        self,
        deployment_id: str,
        hostname: str,
    ) -> CustomDomainInfo:
        """Get status of a custom domain.

        Args:
            deployment_id: Deployment ID
            hostname: Custom domain to check

        Returns:
            CustomDomainInfo with current status
        """
        response = httpx.get(
            f"{self.api_url}/v0/deployments/{deployment_id}/domains/{hostname}",
            headers=self._headers(),
            timeout=30.0,
        )

        if response.status_code >= 400:
            self._handle_error(response)

        return self._parse_domain_info(response.json())

    def remove_custom_domain(
        self,
        deployment_id: str,
        hostname: str,
    ) -> bool:
        """Remove a custom domain from a deployment.

        Args:
            deployment_id: Deployment ID
            hostname: Custom domain to remove

        Returns:
            True if successfully removed
        """
        response = httpx.delete(
            f"{self.api_url}/v0/deployments/{deployment_id}/domains/{hostname}",
            headers=self._headers(),
            timeout=30.0,
        )

        if response.status_code >= 400:
            self._handle_error(response)

        return response.json().get("success", True)

    def _parse_domain_info(self, data: dict[str, Any]) -> CustomDomainInfo:
        """Parse custom domain response.

        Args:
            data: Response data

        Returns:
            CustomDomainInfo
        """
        dns_records = [
            DnsRecord(
                record_type=r["record_type"],
                name=r["name"],
                value=r["value"],
            )
            for r in data.get("dns_records", [])
        ]

        return CustomDomainInfo(
            hostname=data["hostname"],
            configured=data.get("configured", False),
            certificate_status=data.get("certificate_status", "unknown"),
            dns_records=dns_records,
            error=data.get("error"),
            check_url=data.get("check_url"),
        )


ALWAYS_EXCLUDE_PATTERNS = [
    # Git
    ".git",
    ".git/**",
    # Dependencies (these are rebuilt in container)
    "node_modules",
    "node_modules/**",
    "__pycache__",
    "__pycache__/**",
    "*.pyc",
    "*.pyo",
    "*.pyd",
    ".venv",
    ".venv/**",
    "venv",
    "venv/**",
    "env",
    "env/**",
    # Build outputs
    ".next",
    ".next/**",
    "out",
    "out/**",
    "dist",
    "dist/**",
    "build",
    "build/**",
    "*.egg-info",
    "*.egg-info/**",
    # Environment files
    ".env",
    ".env.*",
    "*.env",
    # IDE/Editor
    ".vscode",
    ".vscode/**",
    ".idea",
    ".idea/**",
    "*.swp",
    "*.swo",
    # OS files
    ".DS_Store",
    "Thumbs.db",
    # Testing/coverage
    ".pytest_cache",
    ".pytest_cache/**",
    ".coverage",
    "htmlcov",
    "htmlcov/**",
    ".tox",
    ".tox/**",
    # Type checking
    ".mypy_cache",
    ".mypy_cache/**",
    ".ruff_cache",
    ".ruff_cache/**",
    "*.tsbuildinfo",
    # Logs
    "npm-debug.log*",
    "yarn-debug.log*",
    "yarn-error.log*",
    # Ignore files themselves
    ".runtmignore",
    ".gitignore",
]


def build_ignore_spec(project_path: Path) -> "pathspec.PathSpec":
    """Build a pathspec matcher for the project.

    Combines always-excluded patterns with .runtmignore (preferred)
    or .gitignore. Used by both artifact creation and size validation.

    Args:
        project_path: Path to project directory

    Returns:
        A pathspec.PathSpec matcher
    """
    import pathspec

    patterns: list[str] = ALWAYS_EXCLUDE_PATTERNS.copy()

    runtmignore = project_path / ".runtmignore"
    gitignore = project_path / ".gitignore"

    if runtmignore.exists():
        patterns.extend(_parse_ignore_file(runtmignore))
    elif gitignore.exists():
        patterns.extend(_parse_ignore_file(gitignore))

    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


def create_artifact_zip(project_path: Path) -> Path:
    """Create artifact.zip from project directory.

    Respects .runtmignore file if present, otherwise falls back to .gitignore.
    Always excludes common patterns like node_modules, __pycache__, .git, etc.

    Args:
        project_path: Path to project directory

    Returns:
        Path to created artifact.zip (in temp location)
    """
    import tempfile

    temp_dir = Path(tempfile.mkdtemp())
    zip_path = temp_dir / "artifact.zip"

    spec = build_ignore_spec(project_path)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in project_path.rglob("*"):
            if item.is_file():
                relative = item.relative_to(project_path)
                relative_str = str(relative)

                if not spec.match_file(relative_str):
                    zf.write(item, relative)

    return zip_path


def _parse_ignore_file(ignore_path: Path) -> list[str]:
    """Parse a .gitignore-style ignore file.

    Args:
        ignore_path: Path to ignore file

    Returns:
        List of patterns from the file
    """
    patterns = []
    try:
        content = ignore_path.read_text()
        for line in content.splitlines():
            line = line.strip()
            # Skip empty lines and comments
            if line and not line.startswith("#"):
                patterns.append(line)
    except Exception:
        pass  # Ignore read errors
    return patterns


def compute_src_hash(project_path: Path) -> str:
    """Compute a hash of source files for config-only deploy validation.

    Used to validate that --config-only deploys haven't changed source code.
    If source has changed, the deploy should do a full build, not skip it.

    Strategy:
    1. Try git SHA (preferred - cleaner, faster, handles uncommitted changes)
    2. Fall back to hashing source tree (for non-git projects)

    Args:
        project_path: Path to project directory

    Returns:
        16-character hash string (git SHA prefix or source tree hash)
    """
    import hashlib
    import subprocess

    # Option 1: Use git commit SHA if in git repo
    # This is preferred because it's cleaner and handles the common case
    try:
        # First check if there are uncommitted changes
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project_path,
            capture_output=True,
            text=True,
            timeout=10,
        )

        if status_result.returncode == 0:
            # Get HEAD SHA
            sha_result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=project_path,
                capture_output=True,
                text=True,
                timeout=10,
            )

            if sha_result.returncode == 0:
                git_sha = sha_result.stdout.strip()

                # If there are uncommitted changes, append dirty marker to hash
                has_changes = bool(status_result.stdout.strip())
                if has_changes:
                    # Include hash of uncommitted changes
                    diff_result = subprocess.run(
                        ["git", "diff", "HEAD"],
                        cwd=project_path,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if diff_result.returncode == 0 and diff_result.stdout:
                        # Hash the diff content and combine with SHA
                        diff_hash = hashlib.sha256(diff_result.stdout.encode()).hexdigest()[:8]
                        return f"{git_sha[:8]}-{diff_hash}"

                return git_sha[:16]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass  # Git not available or timeout, fall back to file hash

    # Option 2: Hash source tree (excluding deps/lockfiles/config)
    # This is used for non-git projects
    hasher = hashlib.sha256()

    # Files/dirs to exclude from source hash
    # These don't affect the Docker image content meaningfully
    exclude_patterns = {
        # Dependencies (rebuilt in container)
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".git",
        # Build outputs
        ".next",
        "out",
        "dist",
        "build",
        # Lockfiles (deps changes are handled separately)
        "package-lock.json",
        "bun.lockb",
        "yarn.lock",
        "pnpm-lock.yaml",
        "uv.lock",
        "poetry.lock",
        "Pipfile.lock",
        # Config files (env vars handled via API)
        ".env",
        ".env.local",
        ".env.production",
        "runtm.yaml",
        # IDE/OS
        ".vscode",
        ".idea",
        ".DS_Store",
    }

    def should_exclude(path: Path) -> bool:
        """Check if path should be excluded from hash."""
        for part in path.parts:
            if part in exclude_patterns:
                return True
            # Also check file extensions
            if part.endswith((".pyc", ".pyo", ".pyd")):
                return True
        return False

    # Sort files for deterministic hash
    files = []
    for file in project_path.rglob("*"):
        if file.is_file() and not should_exclude(file.relative_to(project_path)):
            files.append(file)

    for file in sorted(files):
        try:
            # Include relative path in hash (so renames are detected)
            relative_path = str(file.relative_to(project_path))
            hasher.update(relative_path.encode())
            hasher.update(file.read_bytes())
        except Exception:
            pass  # Skip files we can't read

    return hasher.hexdigest()[:16]
