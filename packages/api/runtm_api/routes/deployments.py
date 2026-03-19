"""Deployment API routes."""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from pydantic import BaseModel
from sqlalchemy import and_
from sqlalchemy.orm import Session

from runtm_api.auth import get_auth_context, require_scope
from runtm_api.core.config import Settings, get_settings
from runtm_api.db import BuildLog, Deployment, DeploymentRepository, get_db
from runtm_api.services.idempotency import IdempotencyService
from runtm_shared import (
    ArtifactTooLargeError,
    DeploymentNotFoundError,
    Limits,
    Manifest,
    ManifestValidationError,
    generate_artifact_key,
    generate_deployment_id,
)
from runtm_shared.types import ApiKeyScope, AuthContext, DeploymentState, LogType

router = APIRouter(prefix="/v0/deployments", tags=["deployments"])
logger = logging.getLogger(__name__)


# =============================================================================
# Discovery Response Models (defined first for forward references)
# =============================================================================


class ApiDiscoveryResponse(BaseModel):
    """API-specific discovery info."""

    openapi_path: str = "/openapi.json"
    endpoints: list[str] | None = None


class GeneratedInfoResponse(BaseModel):
    """Metadata about when/how the discovery file was generated."""

    by: str | None = None
    at: datetime | None = None


class AppDiscoveryResponse(BaseModel):
    """App discovery metadata for searchability."""

    description: str | None = None
    summary: str | None = None
    capabilities: list[str] | None = None
    use_cases: list[str] | None = None
    tags: list[str] | None = None
    api: ApiDiscoveryResponse | None = None
    generated: GeneratedInfoResponse | None = None


# =============================================================================
# Response Models
# =============================================================================


class DeploymentResponse(BaseModel):
    """Response model for deployment endpoints."""

    deployment_id: str
    name: str
    state: str
    url: str | None = None
    error_message: str | None = None
    version: int = 1
    is_latest: bool = True
    previous_deployment_id: str | None = None
    app_name: str | None = None  # Fly.io app name (for domain commands)
    template: str | None = None  # Template type (backend-service, static-site, web-app)
    runtime: str | None = None  # Runtime (python, node)
    src_hash: str | None = None  # Source hash for config-only validation
    created_at: datetime
    updated_at: datetime
    ready_at: datetime | None = None  # When deployment became ready (for deploy time calc)
    # Discovery metadata (if available)
    discovery: AppDiscoveryResponse | None = None

    class Config:
        from_attributes = True

    @classmethod
    def from_db(cls, deployment: Deployment) -> DeploymentResponse:
        """Create response from database model."""
        app_name = None
        if deployment.provider_resource:
            app_name = deployment.provider_resource.app_name

        # Extract template and runtime from manifest_json
        template = None
        runtime = None
        if deployment.manifest_json:
            template = deployment.manifest_json.get("template")
            runtime = deployment.manifest_json.get("runtime")

        # Convert discovery_json to response model
        discovery = None
        if deployment.discovery_json:
            api_discovery = None
            if deployment.discovery_json.get("api"):
                api_discovery = ApiDiscoveryResponse(**deployment.discovery_json["api"])
            generated_info = None
            if deployment.discovery_json.get("generated"):
                generated_info = GeneratedInfoResponse(**deployment.discovery_json["generated"])
            discovery = AppDiscoveryResponse(
                description=deployment.discovery_json.get("description"),
                summary=deployment.discovery_json.get("summary"),
                capabilities=deployment.discovery_json.get("capabilities"),
                use_cases=deployment.discovery_json.get("use_cases"),
                tags=deployment.discovery_json.get("tags"),
                api=api_discovery,
                generated=generated_info,
            )

        return cls(
            deployment_id=deployment.deployment_id,
            name=deployment.name,
            state=deployment.state.value
            if isinstance(deployment.state, DeploymentState)
            else deployment.state,
            url=deployment.url,
            error_message=deployment.error_message,
            version=deployment.version,
            is_latest=deployment.is_latest,
            previous_deployment_id=deployment.previous_deployment_id,
            app_name=app_name,
            template=template,
            runtime=runtime,
            src_hash=deployment.src_hash,
            created_at=deployment.created_at,
            updated_at=deployment.updated_at,
            ready_at=deployment.ready_at,
            discovery=discovery,
        )


class LogEntry(BaseModel):
    """Single log entry."""

    log_type: str
    content: str
    created_at: datetime


class LogsResponse(BaseModel):
    """Response model for logs endpoint."""

    deployment_id: str
    logs: list[LogEntry]
    source: str = "stored"
    instructions: str | None = None


class CreateDeploymentResponse(BaseModel):
    """Response for deployment creation."""

    deployment_id: str
    status: str
    message: str


class RedeployInfo(BaseModel):
    """Information about a redeployment."""

    is_redeploy: bool = False
    previous_deployment_id: str | None = None
    previous_version: int | None = None
    new_version: int | None = None


class DeploymentsListResponse(BaseModel):
    """Response model for list deployments endpoint."""

    deployments: list[DeploymentResponse]
    total: int


class DnsRecordResponse(BaseModel):
    """DNS record for custom domain setup."""

    record_type: str
    name: str
    value: str


class CustomDomainRequest(BaseModel):
    """Request model for adding a custom domain."""

    hostname: str


class CustomDomainResponse(BaseModel):
    """Response model for custom domain operations."""

    hostname: str
    configured: bool = False
    certificate_status: str = "pending"
    dns_records: list[DnsRecordResponse] = []
    error: str | None = None
    check_url: str | None = None


class RemoveDomainResponse(BaseModel):
    """Response model for domain removal."""

    success: bool
    message: str


class DeploymentSearchResult(BaseModel):
    """Search result with deployment and discovery metadata."""

    deployment_id: str
    name: str
    state: str
    url: str | None = None
    template: str | None = None
    runtime: str | None = None
    version: int = 1
    is_latest: bool = True
    created_at: datetime
    updated_at: datetime
    discovery: AppDiscoveryResponse | None = None
    match_score: float = 0.0

    class Config:
        from_attributes = True

    @classmethod
    def from_db(cls, deployment: Deployment, match_score: float = 0.0) -> DeploymentSearchResult:
        """Create search result from database model."""
        # Extract template and runtime from manifest_json
        template = None
        runtime = None
        if deployment.manifest_json:
            template = deployment.manifest_json.get("template")
            runtime = deployment.manifest_json.get("runtime")

        # Convert discovery_json to response model
        discovery = None
        if deployment.discovery_json:
            api_discovery = None
            if deployment.discovery_json.get("api"):
                api_discovery = ApiDiscoveryResponse(**deployment.discovery_json["api"])
            generated_info = None
            if deployment.discovery_json.get("generated"):
                generated_info = GeneratedInfoResponse(**deployment.discovery_json["generated"])
            discovery = AppDiscoveryResponse(
                description=deployment.discovery_json.get("description"),
                summary=deployment.discovery_json.get("summary"),
                capabilities=deployment.discovery_json.get("capabilities"),
                use_cases=deployment.discovery_json.get("use_cases"),
                tags=deployment.discovery_json.get("tags"),
                api=api_discovery,
                generated=generated_info,
            )

        return cls(
            deployment_id=deployment.deployment_id,
            name=deployment.name,
            state=deployment.state.value
            if isinstance(deployment.state, DeploymentState)
            else deployment.state,
            url=deployment.url,
            template=template,
            runtime=runtime,
            version=deployment.version,
            is_latest=deployment.is_latest,
            created_at=deployment.created_at,
            updated_at=deployment.updated_at,
            discovery=discovery,
            match_score=match_score,
        )


class SearchResponse(BaseModel):
    """Response model for search endpoint."""

    results: list[DeploymentSearchResult]
    total: int
    query: str


# =============================================================================
# Endpoints
# =============================================================================


@router.get(
    "", response_model=DeploymentsListResponse, dependencies=[require_scope(ApiKeyScope.READ)]
)
async def list_deployments(
    state: str | None = None,
    name: str | None = Query(None, description="Filter by project name"),
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> DeploymentsListResponse:
    """List all deployments.

    Query params:
    - state: Filter by state (queued, building, deploying, ready, failed, destroyed)
    - name: Filter by project name (exact match)
    - limit: Maximum number of results (default: 50, max: 100)
    - offset: Pagination offset (default: 0)

    Returns list of deployments ordered by created_at (newest first).
    Tenant isolation is enforced via tenant_id from auth context.
    """
    # Validate limit
    if limit > 100:
        limit = 100
    if limit < 1:
        limit = 50

    # Use repository for tenant-scoped queries
    repo = DeploymentRepository(db, auth.tenant_id)

    # Build base query with tenant scoping
    query = repo._scoped_query()

    # Filter by name if provided
    if name:
        query = query.filter(Deployment.name == name)

    # Filter by state if provided
    if state:
        try:
            state_enum = DeploymentState(state)
            query = query.filter(Deployment.state == state_enum)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": f"Invalid state: {state}. Must be one of: queued, building, deploying, ready, failed, destroyed"
                },
            )

    # Get total count
    total = query.count()

    # Apply pagination and ordering
    deployments = query.order_by(Deployment.created_at.desc()).offset(offset).limit(limit).all()

    return DeploymentsListResponse(
        deployments=[DeploymentResponse.from_db(dep) for dep in deployments],
        total=total,
    )


@router.get(
    "/search", response_model=SearchResponse, dependencies=[require_scope(ApiKeyScope.READ)]
)
async def search_deployments(
    q: str = Query(..., min_length=1, description="Search query"),
    state: str | None = Query(None, description="Filter by state"),
    template: str | None = Query(None, description="Filter by template type"),
    limit: int = Query(20, ge=1, le=100, description="Maximum results"),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> SearchResponse:
    """Search deployments by discovery metadata.

    Searches across description, summary, tags, capabilities, and use_cases
    in the runtm.discovery.yaml metadata.

    Query params:
    - q: Search query (required, searches description, summary, tags, capabilities, use_cases)
    - state: Filter by state (optional)
    - template: Filter by template type (optional)
    - limit: Maximum results (default: 20, max: 100)

    Returns deployments with discovery metadata, ordered by relevance.
    Tenant isolation is enforced via tenant_id from auth context.
    """
    from sqlalchemy import String, cast, func, or_

    # Use repository for tenant scoping
    repo = DeploymentRepository(db, auth.tenant_id)

    # Build base query - only search deployments with discovery metadata (tenant-scoped)
    query = repo._scoped_query().filter(Deployment.discovery_json.isnot(None))

    # Filter by state if provided
    if state:
        try:
            state_enum = DeploymentState(state)
            query = query.filter(Deployment.state == state_enum)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": f"Invalid state: {state}"},
            )

    # Filter by template if provided
    if template:
        query = query.filter(Deployment.manifest_json["template"].astext == template)

    # Only show latest versions by default
    query = query.filter(Deployment.is_latest)

    # Search query - case-insensitive text search across discovery fields
    search_term = f"%{q.lower()}%"

    # Build search conditions for JSONB fields
    # PostgreSQL JSONB text search using ->> (text extraction) and ILIKE
    search_conditions = or_(
        # Search in description
        func.lower(Deployment.discovery_json["description"].astext).like(search_term),
        # Search in summary
        func.lower(Deployment.discovery_json["summary"].astext).like(search_term),
        # Search in tags array (convert to text and search)
        func.lower(cast(Deployment.discovery_json["tags"], String)).like(search_term),
        # Search in capabilities array
        func.lower(cast(Deployment.discovery_json["capabilities"], String)).like(search_term),
        # Search in use_cases array
        func.lower(cast(Deployment.discovery_json["use_cases"], String)).like(search_term),
        # Also search in deployment name
        func.lower(Deployment.name).like(search_term),
    )

    query = query.filter(search_conditions)

    # Get total count
    total = query.count()

    # Order by created_at (newest first) and apply limit
    deployments = query.order_by(Deployment.created_at.desc()).limit(limit).all()

    # Calculate match scores (simple relevance based on where match was found)
    results = []
    for dep in deployments:
        score = 0.0
        q_lower = q.lower()

        if dep.discovery_json:
            # Higher score for exact tag matches
            tags = dep.discovery_json.get("tags", []) or []
            if any(q_lower == tag.lower() for tag in tags):
                score += 1.0
            elif any(q_lower in tag.lower() for tag in tags):
                score += 0.5

            # Score for summary match (most visible)
            summary = dep.discovery_json.get("summary", "") or ""
            if q_lower in summary.lower():
                score += 0.8

            # Score for description match
            description = dep.discovery_json.get("description", "") or ""
            if q_lower in description.lower():
                score += 0.6

            # Score for capabilities match
            capabilities = dep.discovery_json.get("capabilities", []) or []
            if any(q_lower in cap.lower() for cap in capabilities):
                score += 0.4

            # Score for use_cases match
            use_cases = dep.discovery_json.get("use_cases", []) or []
            if any(q_lower in uc.lower() for uc in use_cases):
                score += 0.3

        # Score for name match
        if q_lower in dep.name.lower():
            score += 0.2

        results.append(DeploymentSearchResult.from_db(dep, match_score=score))

    # Sort by match score (highest first), then by created_at
    results.sort(key=lambda r: (-r.match_score, r.created_at), reverse=False)

    return SearchResponse(
        results=results,
        total=total,
        query=q,
    )


@router.post(
    "",
    response_model=DeploymentResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[require_scope(ApiKeyScope.DEPLOY)],
)
async def create_deployment(
    request: Request,
    manifest: UploadFile = File(..., description="runtm.yaml manifest file"),
    artifact: UploadFile = File(..., description="artifact.zip containing project files"),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    force_new: bool = Query(
        False, alias="new", description="Force new deployment instead of redeploying"
    ),
    tier: str | None = Query(
        None, description="Machine tier override: starter, standard, or performance"
    ),
    config_only: bool = Query(
        False, description="Skip Docker build - reuse previous image (for env/tier changes only)"
    ),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
    settings: Settings = Depends(get_settings),
) -> DeploymentResponse:
    """Create a new deployment or redeploy an existing one.

    Accepts multipart form with:
    - manifest: runtm.yaml file
    - artifact: zip file containing project
    - secrets: (optional) JSON string of secrets to inject to the provider
    - src_hash: (optional) Source hash for tracking and config-only validation

    Redeployment behavior:
    - If a deployment with the same name exists and is ready/failed, this will
      create a new version that updates the existing infrastructure.
    - The URL stays the same across versions.
    - Use ?new=true to force a completely new deployment.
    - Use ?config_only=true to skip Docker build and reuse previous image.

    Returns deployment info with status.

    Supports idempotency via Idempotency-Key header - if the same key
    is used again, returns the existing deployment instead of creating a new one.

    Security note on secrets:
    - Secrets are passed through to the deployment provider (e.g., Fly.io)
    - Secrets are NEVER stored in the Runtm database
    - Only secret NAMES are logged for debugging purposes
    """
    import json

    # Parse secrets and metadata from form data
    # Secrets are passed through to worker, never stored in DB
    secrets_to_inject: dict | None = None
    src_hash: str | None = None
    form = await request.form()

    secrets_json = form.get("secrets")
    if secrets_json:
        try:
            secrets_to_inject = json.loads(secrets_json)
            if secrets_to_inject:
                # Log only secret NAMES, never values
                secret_names = list(secrets_to_inject.keys())
                logger.info("Secrets to inject: %s", secret_names)
        except json.JSONDecodeError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": "Invalid secrets JSON format"},
            )

    # Extract src_hash from form data
    src_hash_value = form.get("src_hash")
    if src_hash_value:
        src_hash = str(src_hash_value)
    # Check idempotency first
    if idempotency_key:
        idempotency_service = IdempotencyService(db)
        existing = idempotency_service.get_existing_deployment(idempotency_key)
        if existing:
            return DeploymentResponse.from_db(existing)

    # Read and validate manifest
    try:
        manifest_content = await manifest.read()
        manifest_str = manifest_content.decode("utf-8")
        parsed_manifest = Manifest.from_yaml(manifest_str)
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "Manifest must be valid UTF-8 text"},
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ManifestValidationError(str(e)).to_dict(),
        )

    # Apply tier override if provided via query param
    if tier is not None:
        from runtm_shared.types import MachineTier

        try:
            # Validate the tier
            MachineTier(tier)
            # Create new manifest with overridden tier
            parsed_manifest = Manifest(
                name=parsed_manifest.name,
                template=parsed_manifest.template,
                runtime=parsed_manifest.runtime,
                health_path=parsed_manifest.health_path,
                port=parsed_manifest.port,
                tier=tier,
            )
        except ValueError:
            valid_tiers = ", ".join(t.value for t in MachineTier)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": f"Invalid tier: {tier}. Must be one of: {valid_tiers}"},
            )

    # =========================================================================
    # Policy Check (rate limits, app limits, tier allowlist)
    # =========================================================================
    from runtm_api.services.policy import get_policy_provider
    from runtm_shared.deploy_tracking import release_concurrent_deploy, reserve_concurrent_deploy
    from runtm_shared.redis import get_redis_client_or_warn

    policy = get_policy_provider()
    tier_to_check = tier or parsed_manifest.tier or "starter"

    # Check policy limits (except concurrent - handled separately with atomic Redis)
    # Pass app_name to allow redeploys when at app limit
    check_result = policy.check_deploy(
        auth.tenant_id, db, requested_tier=tier_to_check, app_name=parsed_manifest.name
    )
    if not check_result.allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": check_result.reason, "code": "policy_denied"},
        )

    deployment_expires_at = check_result.expires_at
    limits = check_result.limits

    # Atomic concurrent deploy reservation
    # IMPORTANT: We reserve BEFORE doing expensive operations
    redis_client = get_redis_client_or_warn()
    reserved_slot = False

    if redis_client and limits and limits.concurrent_deploys is not None:
        # Generate deployment ID early so we can track the reservation
        deployment_id = generate_deployment_id()
        allowed, _count = reserve_concurrent_deploy(
            redis_client, auth.tenant_id, limits.concurrent_deploys, deployment_id
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": f"Concurrent deploy limit ({limits.concurrent_deploys}). "
                    "Wait for current deploy to finish.",
                    "code": "concurrent_limit_exceeded",
                },
            )
        reserved_slot = True
    else:
        # No concurrent limit - generate ID now
        deployment_id = generate_deployment_id()

    # From here on, we must release the slot on failure (before enqueue succeeds)
    # After successful enqueue, worker owns the release - do NOT release here
    try:
        # Read artifact and check size
        artifact_content = await artifact.read()
        artifact_size = len(artifact_content)

        if artifact_size > Limits.MAX_ARTIFACT_SIZE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=ArtifactTooLargeError(
                    size_bytes=artifact_size,
                    max_bytes=Limits.MAX_ARTIFACT_SIZE_BYTES,
                ).to_dict(),
            )

        # Check for existing deployment with same name (for redeployment)
        # The unique constraint requires only one is_latest=True per (tenant_id, name) (excluding destroyed/failed)
        # So we need to find and mark any existing is_latest deployment BEFORE creating the new one
        previous_deployment = None
        is_redeploy = False

        # =========================================================================
        # Version Tracking: Get max version from ALL deployments with same name
        # =========================================================================
        # This ensures version always increments, even after failed deployments
        # Version tracking is separate from infrastructure reuse (redeploy)
        from sqlalchemy import func as sql_func

        max_version_result = (
            db.query(sql_func.max(Deployment.version))
            .filter(
                Deployment.tenant_id == auth.tenant_id,
                Deployment.name == parsed_manifest.name,
            )
            .scalar()
        )
        previous_version = max_version_result or 0

        # =========================================================================
        # Infrastructure Reuse: Find deployment to redeploy from
        # =========================================================================
        # Always check for existing is_latest deployment to avoid constraint violations
        # (even with force_new=True, we need to mark existing as not latest)
        # Build the filter within tenant scope
        update_filter = and_(
            Deployment.tenant_id == auth.tenant_id,  # Tenant isolation
            Deployment.name == parsed_manifest.name,
            Deployment.is_latest == True,  # noqa: E712
            Deployment.state.notin_([DeploymentState.DESTROYED, DeploymentState.FAILED]),
        )

        # Atomically mark ALL matching deployments as not latest
        # This ensures the unique constraint is satisfied before we insert the new one
        # Use SELECT FOR UPDATE to lock rows and prevent race conditions
        existing_latest = db.query(Deployment).filter(update_filter).with_for_update().first()

        # If no is_latest deployment found, check for any READY deployment with the same name
        # This handles the edge case where the latest was destroyed but there's still
        # a READY deployment that should be used as the base for redeployment
        if not existing_latest and not force_new:
            any_ready = (
                db.query(Deployment)
                .filter(
                    Deployment.tenant_id == auth.tenant_id,
                    Deployment.name == parsed_manifest.name,
                    Deployment.state == DeploymentState.READY,
                )
                .order_by(Deployment.created_at.desc())
                .with_for_update()
                .first()
            )
            if any_ready:
                existing_latest = any_ready
                # Mark it as latest first (it will be unmarked below)
                any_ready.is_latest = True
                logger.info(
                    "Found READY deployment %s to redeploy (recovering from destroyed is_latest)",
                    any_ready.deployment_id,
                )

        if existing_latest:
            previous_deployment = existing_latest

            # Only treat as redeploy if:
            # 1. Not forcing new deployment (force_new=False)
            # 2. Previous deployment is in a terminal state (READY or FAILED)
            # This determines whether we reuse infrastructure
            if not force_new and existing_latest.state in (
                DeploymentState.READY,
                DeploymentState.FAILED,
            ):
                is_redeploy = True
                logger.info(
                    "Redeploying %s (v%d -> v%d, previous state: %s)",
                    parsed_manifest.name,
                    previous_version,
                    previous_version + 1,
                    existing_latest.state.value,
                )
            else:
                logger.info(
                    "Creating new deployment %s (v%d -> v%d, marking previous v%d as not latest)",
                    parsed_manifest.name,
                    previous_version,
                    previous_version + 1,
                    previous_version,
                )

            # Mark as not latest (we already have the row locked from SELECT FOR UPDATE)
            existing_latest.is_latest = False
            db.flush()  # Ensure the update is persisted before creating new deployment

        # Use deployment_id generated earlier (before reservation)
        artifact_key = generate_artifact_key(deployment_id)

        # Store artifact via configured backend (local disk or S3/Tigris)
        # Runs in a thread to avoid blocking the async event loop during
        # network I/O (boto3 is synchronous; local file writes are fast
        # but S3 uploads can take seconds).
        import asyncio

        from runtm_shared.storage import get_artifact_store

        store = get_artifact_store(
            backend=settings.artifact_storage_backend,
            storage_path=settings.artifact_storage_path,
            s3_bucket=settings.s3_bucket,
            s3_endpoint_url=settings.s3_endpoint_url or None,
            s3_region=settings.s3_region,
        )
        await asyncio.to_thread(store.put, artifact_key, artifact_content)

        # Create deployment record with tenant isolation
        deployment = Deployment(
            deployment_id=deployment_id,
            tenant_id=auth.tenant_id,  # Tenant isolation
            owner_id=auth.principal_id,  # Principal (user/service account)
            api_key_id=auth.api_key_id,
            name=parsed_manifest.name,
            state=DeploymentState.QUEUED,
            artifact_key=artifact_key,
            manifest_json=parsed_manifest.to_dict(),
            version=previous_version + 1,
            is_latest=True,
            previous_deployment_id=previous_deployment.deployment_id
            if previous_deployment
            else None,
            src_hash=src_hash,
            config_only=config_only,
            expires_at=deployment_expires_at,  # Set from policy check
        )
        db.add(deployment)
        db.flush()  # Get the ID

        # Store idempotency key if provided
        if idempotency_key:
            idempotency_service = IdempotencyService(db)
            idempotency_service.store_key(idempotency_key, deployment.id)

        db.commit()
        db.refresh(deployment)

        # Enqueue deployment job to worker
        # Pass redeploy info if this is an update
        # Secrets are passed through to worker (never stored in DB)
        from runtm_api.services.queue import enqueue_deployment

        redeploy_from = previous_deployment.deployment_id if is_redeploy else None
        enqueue_deployment(
            deployment_id,
            settings.redis_url,
            redeploy_from=redeploy_from,
            secrets=secrets_to_inject,
            config_only=config_only,
        )

        # SUCCESS: Do NOT release slot here - worker owns release after enqueue
        return DeploymentResponse.from_db(deployment)

    except Exception:
        # FAILURE: Release slot since worker won't run
        if reserved_slot and redis_client:
            release_concurrent_deploy(redis_client, auth.tenant_id, deployment_id)
        raise


# =============================================================================
# Custom Domain Endpoints (must come before /{deployment_id} to match correctly)
# =============================================================================


@router.post(
    "/{deployment_id}/domains",
    response_model=CustomDomainResponse,
    dependencies=[require_scope(ApiKeyScope.DEPLOY)],
)
async def add_custom_domain(
    deployment_id: str,
    request: CustomDomainRequest,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> CustomDomainResponse:
    """Add a custom domain to a deployment.

    Creates a certificate for the hostname and returns DNS configuration.
    Only works for deployments in 'ready' state.
    Requires DEPLOY scope.

    Returns DNS records that need to be configured at your domain registrar.
    """
    logger.info("Adding custom domain %s to deployment %s", request.hostname, deployment_id)

    # Use repository for tenant-scoped lookup
    repo = DeploymentRepository(db, auth.tenant_id)
    deployment = repo.get_by_deployment_id(deployment_id)

    if not deployment:
        logger.warning("Deployment %s not found or wrong tenant", deployment_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=DeploymentNotFoundError(deployment_id).to_dict(),
        )

    # Check if deployment is ready
    if deployment.state != DeploymentState.READY:
        logger.warning("Deployment %s not ready (state: %s)", deployment_id, deployment.state.value)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": f"Cannot add domain to deployment in '{deployment.state.value}' state",
                "recovery_hint": "Wait for the deployment to be ready, then try again.",
            },
        )

    # Check if provider resource exists
    if not deployment.provider_resource:
        logger.warning("Deployment %s has no provider resource", deployment_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "No provider resource found for deployment",
                "recovery_hint": "The deployment may not have completed successfully. Try redeploying.",
            },
        )

    # Add custom domain via provider
    try:
        from runtm_shared.errors import ProviderNotConfiguredError
        from runtm_shared.types import ProviderResource as ProviderResourceType
        from runtm_worker.providers.fly import FlyProvider

        try:
            provider = FlyProvider()
        except ProviderNotConfiguredError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "Fly.io provider is not configured",
                    "recovery_hint": f"{e.recovery_hint or 'Set FLY_API_TOKEN environment variable'}",
                },
            ) from e

        provider_resource = ProviderResourceType(
            app_name=deployment.provider_resource.app_name,
            machine_id=deployment.provider_resource.machine_id,
            region=deployment.provider_resource.region,
            image_ref=deployment.provider_resource.image_ref,
            url=deployment.url or "",
        )

        logger.info("Calling provider.add_custom_domain for %s", request.hostname)
        domain_info = provider.add_custom_domain(provider_resource, request.hostname)

        return CustomDomainResponse(
            hostname=domain_info.hostname,
            configured=domain_info.configured,
            certificate_status=domain_info.certificate_status,
            dns_records=[
                DnsRecordResponse(
                    record_type=r.record_type,
                    name=r.name,
                    value=r.value,
                )
                for r in domain_info.dns_records
            ],
            error=domain_info.error,
            check_url=domain_info.check_url,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to add custom domain for %s: %s", deployment_id, e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": f"Failed to add custom domain: {str(e)}",
                "recovery_hint": "Check the provider configuration and try again.",
            },
        ) from e


@router.get(
    "/{deployment_id}/domains/{hostname}",
    response_model=CustomDomainResponse,
    dependencies=[require_scope(ApiKeyScope.READ)],
)
async def get_custom_domain_status(
    deployment_id: str,
    hostname: str,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> CustomDomainResponse:
    """Get status of a custom domain.

    Shows certificate status and any required DNS configuration.
    """
    # Use repository for tenant-scoped lookup
    repo = DeploymentRepository(db, auth.tenant_id)
    deployment = repo.get_by_deployment_id(deployment_id)

    if not deployment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=DeploymentNotFoundError(deployment_id).to_dict(),
        )

    # Check if provider resource exists
    if not deployment.provider_resource:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "No provider resource found for deployment",
                "recovery_hint": "The deployment may not have completed successfully.",
            },
        )

    # Get domain status from provider
    try:
        from runtm_shared.errors import ProviderNotConfiguredError
        from runtm_shared.types import ProviderResource as ProviderResourceType
        from runtm_worker.providers.fly import FlyProvider

        try:
            provider = FlyProvider()
        except ProviderNotConfiguredError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "Fly.io provider is not configured",
                    "recovery_hint": f"{e.recovery_hint or 'Set FLY_API_TOKEN environment variable'}",
                },
            ) from e
        provider_resource = ProviderResourceType(
            app_name=deployment.provider_resource.app_name,
            machine_id=deployment.provider_resource.machine_id,
            region=deployment.provider_resource.region,
            image_ref=deployment.provider_resource.image_ref,
            url=deployment.url or "",
        )

        domain_info = provider.get_custom_domain_status(provider_resource, hostname)

        return CustomDomainResponse(
            hostname=domain_info.hostname,
            configured=domain_info.configured,
            certificate_status=domain_info.certificate_status,
            dns_records=[
                DnsRecordResponse(
                    record_type=r.record_type,
                    name=r.name,
                    value=r.value,
                )
                for r in domain_info.dns_records
            ],
            error=domain_info.error,
            check_url=domain_info.check_url,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get domain status for %s: %s", deployment_id, e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": f"Failed to get domain status: {str(e)}",
                "recovery_hint": "Check the provider configuration and try again.",
            },
        ) from e


@router.delete(
    "/{deployment_id}/domains/{hostname}",
    response_model=RemoveDomainResponse,
    dependencies=[require_scope(ApiKeyScope.DELETE)],
)
async def remove_custom_domain(
    deployment_id: str,
    hostname: str,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> RemoveDomainResponse:
    """Remove a custom domain from a deployment.

    Deletes the certificate and removes the domain configuration.
    Requires DELETE scope.
    """
    # Use repository for tenant-scoped lookup
    repo = DeploymentRepository(db, auth.tenant_id)
    deployment = repo.get_by_deployment_id(deployment_id)

    if not deployment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=DeploymentNotFoundError(deployment_id).to_dict(),
        )

    # Check if provider resource exists
    if not deployment.provider_resource:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "No provider resource found for deployment",
                "recovery_hint": "The deployment may not have completed successfully.",
            },
        )

    # Remove domain from provider
    try:
        from runtm_shared.errors import ProviderNotConfiguredError
        from runtm_shared.types import ProviderResource as ProviderResourceType
        from runtm_worker.providers.fly import FlyProvider

        try:
            provider = FlyProvider()
        except ProviderNotConfiguredError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "Fly.io provider is not configured",
                    "recovery_hint": f"{e.recovery_hint or 'Set FLY_API_TOKEN environment variable'}",
                },
            ) from e
        provider_resource = ProviderResourceType(
            app_name=deployment.provider_resource.app_name,
            machine_id=deployment.provider_resource.machine_id,
            region=deployment.provider_resource.region,
            image_ref=deployment.provider_resource.image_ref,
            url=deployment.url or "",
        )

        success = provider.remove_custom_domain(provider_resource, hostname)

        if success:
            return RemoveDomainResponse(
                success=True,
                message=f"Domain {hostname} removed successfully",
            )
        else:
            return RemoveDomainResponse(
                success=False,
                message=f"Failed to remove domain {hostname}",
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to remove domain for %s: %s", deployment_id, e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": f"Failed to remove domain: {str(e)}",
                "recovery_hint": "Check the provider configuration and try again.",
            },
        ) from e


@router.get(
    "/{deployment_id}",
    response_model=DeploymentResponse,
    dependencies=[require_scope(ApiKeyScope.READ)],
)
async def get_deployment(
    deployment_id: str,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> DeploymentResponse:
    """Get deployment status and info.

    Returns current status, URL (if ready), and any error messages.
    Returns 404 if not found or wrong tenant (prevents enumeration).
    """
    # Use repository for tenant-scoped lookup (returns 404 not 403)
    repo = DeploymentRepository(db, auth.tenant_id)
    deployment = repo.get_by_deployment_id(deployment_id)

    if not deployment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=DeploymentNotFoundError(deployment_id).to_dict(),
        )

    return DeploymentResponse.from_db(deployment)


@router.get(
    "/{deployment_id}/logs",
    response_model=LogsResponse,
    dependencies=[require_scope(ApiKeyScope.READ)],
)
async def get_deployment_logs(
    deployment_id: str,
    log_type: str | None = Query(None, alias="type"),
    lines: int = Query(20, alias="lines", ge=1, le=500),
    search: str | None = Query(None, alias="search"),
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
) -> LogsResponse:
    """Get deployment logs.

    Query params:
    - type: "build", "deploy", or "runtime" (optional, returns all + runtime tail if not specified)
    - lines: Number of runtime log lines to include (default: 20, max: 500)
    - search: Filter logs containing this text (case-insensitive)

    For runtime logs, returns best-effort logs from provider or instructions
    on how to get them directly.
    """
    # Use repository for tenant-scoped lookup
    repo = DeploymentRepository(db, auth.tenant_id)
    deployment = repo.get_by_deployment_id(deployment_id)

    if not deployment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=DeploymentNotFoundError(deployment_id).to_dict(),
        )

    # Handle runtime logs specially
    if log_type == "runtime":
        # Check if deployment is ready
        if deployment.state != DeploymentState.READY:
            return LogsResponse(
                deployment_id=deployment_id,
                logs=[],
                source="unavailable",
                instructions="Deployment is not ready. Runtime logs are only available for running deployments.",
            )

        # Get provider resource and fetch logs from Fly.io
        if deployment.provider_resource:
            try:
                from runtm_shared.types import ProviderResource as ProviderResourceType
                from runtm_worker.providers.fly import FlyProvider

                provider = FlyProvider()
                provider_resource = ProviderResourceType(
                    app_name=deployment.provider_resource.app_name,
                    machine_id=deployment.provider_resource.machine_id,
                    region=deployment.provider_resource.region,
                    image_ref=deployment.provider_resource.image_ref,
                    url=deployment.url or "",
                )

                runtime_logs = provider.get_logs(provider_resource, lines=100)

                # Check if we got actual logs (not error messages)
                error_prefixes = ("Failed to fetch", "Error fetching", "Timeout fetching", "App ")
                is_error = not runtime_logs or any(
                    runtime_logs.startswith(p) for p in error_prefixes
                )

                if not is_error:
                    return LogsResponse(
                        deployment_id=deployment_id,
                        logs=[
                            LogEntry(
                                log_type="runtime",
                                content=runtime_logs,
                                created_at=datetime.utcnow(),
                            )
                        ],
                        source="fly",
                    )
                else:
                    # Log fetch failed - include error in instructions
                    app_name = deployment.provider_resource.app_name
                    return LogsResponse(
                        deployment_id=deployment_id,
                        logs=[],
                        source="fly",
                        instructions=f"Could not fetch logs via API. Use the Fly.io CLI:\n\n  fly logs -a {app_name}\n\nInstall flyctl: curl -L https://fly.io/install.sh | sh\n\nAPI response: {runtime_logs}",
                    )
            except Exception as e:
                logger.warning("Failed to fetch runtime logs for %s: %s", deployment_id, e)
                app_name = deployment.provider_resource.app_name
                return LogsResponse(
                    deployment_id=deployment_id,
                    logs=[],
                    source="fly",
                    instructions=f"Runtime logs are available via the Fly.io CLI:\n\n  fly logs -a {app_name}\n\nInstall flyctl: curl -L https://fly.io/install.sh | sh",
                )

        return LogsResponse(
            deployment_id=deployment_id,
            logs=[],
            source="unavailable",
            instructions="Provider resource not found. Try checking deployment status.",
        )

    # Query build/deploy logs
    query = db.query(BuildLog).filter(BuildLog.deployment_id == deployment.id)

    if log_type:
        try:
            log_type_enum = LogType(log_type)
            query = query.filter(BuildLog.log_type == log_type_enum)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": f"Invalid log type: {log_type}. Must be 'build', 'deploy', or 'runtime'"
                },
            )

    db_logs = query.order_by(BuildLog.created_at).all()

    # Convert DB logs to LogEntry
    log_entries = [
        LogEntry(
            log_type=log.log_type.value if isinstance(log.log_type, LogType) else log.log_type,
            content=log.content,
            created_at=log.created_at,
        )
        for log in db_logs
    ]

    # If no specific type requested and deployment is ready, also fetch runtime logs
    if (
        log_type is None
        and deployment.state == DeploymentState.READY
        and deployment.provider_resource
    ):
        try:
            from runtm_shared.types import ProviderResource as ProviderResourceType
            from runtm_worker.providers.fly import FlyProvider

            provider = FlyProvider()
            provider_resource = ProviderResourceType(
                app_name=deployment.provider_resource.app_name,
                machine_id=deployment.provider_resource.machine_id,
                region=deployment.provider_resource.region,
                image_ref=deployment.provider_resource.image_ref,
                url=deployment.url or "",
            )

            runtime_logs = provider.get_logs(provider_resource, lines=lines)

            # Check if we got actual logs (not error messages)
            error_prefixes = (
                "Failed to fetch",
                "Error fetching",
                "Timeout fetching",
                "flyctl not installed",
            )
            is_error = not runtime_logs or any(runtime_logs.startswith(p) for p in error_prefixes)

            if not is_error:
                log_entries.append(
                    LogEntry(
                        log_type="runtime",
                        content=runtime_logs,
                        created_at=datetime.utcnow(),
                    )
                )
        except Exception as e:
            logger.warning("Failed to fetch runtime logs for default view: %s", e)

    # Apply search filter if specified
    # Supports:
    #   - Single term: "error"
    #   - Multiple terms (OR): "error,warning,failed"
    #   - Regex patterns: "error.*database" or "HTTP/1.1\" [45]\d\d"
    if search:
        import re

        # Check if it looks like a regex (contains regex metacharacters)
        regex_chars = set(r".*+?^${}[]|()\\")
        is_regex = any(c in search for c in regex_chars)

        if is_regex:
            try:
                pattern = re.compile(search, re.IGNORECASE)
                log_entries = [log for log in log_entries if pattern.search(log.content)]
            except re.error:
                # Invalid regex, fall back to literal search
                search_lower = search.lower()
                log_entries = [log for log in log_entries if search_lower in log.content.lower()]
        else:
            # Multiple terms separated by comma = OR logic
            terms = [t.strip().lower() for t in search.split(",")]
            log_entries = [
                log for log in log_entries if any(term in log.content.lower() for term in terms)
            ]

    return LogsResponse(
        deployment_id=deployment_id,
        logs=log_entries,
        source="stored",
    )


class DestroyResponse(BaseModel):
    """Response model for destroy endpoint."""

    deployment_id: str
    status: str
    message: str


@router.delete(
    "/{deployment_id}",
    response_model=DestroyResponse,
    dependencies=[require_scope(ApiKeyScope.DELETE)],
)
async def destroy_deployment(
    deployment_id: str,
    db: Session = Depends(get_db),
    auth: AuthContext = Depends(get_auth_context),
    settings: Settings = Depends(get_settings),
) -> DestroyResponse:
    """Destroy a deployment.

    Stops and removes the deployed machine, then marks the deployment as destroyed.
    Also cleans up DNS records if custom domain is configured.
    Only deployments in ready or failed state can be destroyed.
    Requires DELETE scope.
    """
    # Use repository for tenant-scoped lookup
    repo = DeploymentRepository(db, auth.tenant_id)
    deployment = repo.get_by_deployment_id(deployment_id)

    if not deployment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=DeploymentNotFoundError(deployment_id).to_dict(),
        )

    # Check if deployment can be destroyed
    destroyable_states = (
        DeploymentState.QUEUED,
        DeploymentState.READY,
        DeploymentState.FAILED,
    )
    if deployment.state not in destroyable_states:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": f"Cannot destroy deployment in '{deployment.state.value}' state",
                "recovery_hint": "Wait for the build/deploy to complete or fail, then try again.",
            },
        )

    # Destroy provider resources if they exist
    provider_destroyed = False
    dns_cleaned = False
    if deployment.provider_resource and deployment.state == DeploymentState.READY:
        try:
            from runtm_worker.providers.fly import FlyProvider

            provider = FlyProvider()
            resource = deployment.provider_resource

            # Build ProviderResource from DB model
            from runtm_shared.types import ProviderResource as ProviderResourceType

            provider_resource = ProviderResourceType(
                app_name=resource.app_name,
                machine_id=resource.machine_id,
                region=resource.region,
                image_ref=resource.image_ref,
                url=deployment.url or "",
            )
            provider_destroyed = provider.destroy(provider_resource)

            # Clean up DNS record if custom domain is configured
            try:
                from runtm_shared.urls import get_base_domain

                base_domain = get_base_domain()
                if base_domain and settings.dns_enabled:
                    if settings.dns_provider == "cloudflare":
                        from runtm_shared.dns.cloudflare import CloudflareDnsProvider

                        if settings.cloudflare_api_token and settings.cloudflare_zone_id:
                            dns_provider = CloudflareDnsProvider(
                                api_token=settings.cloudflare_api_token,
                                zone_id=settings.cloudflare_zone_id,
                            )
                            dns_cleaned = dns_provider.delete_record(
                                subdomain=resource.app_name,
                                domain=base_domain,
                            )
                            if dns_cleaned:
                                logger.info(
                                    "DNS record deleted for %s.%s", resource.app_name, base_domain
                                )
            except Exception as dns_error:
                logger.warning("Failed to delete DNS record for %s: %s", deployment_id, dns_error)

        except Exception as e:
            # Log but don't fail - we still mark as destroyed
            logger.warning("Failed to destroy provider resource for %s: %s", deployment_id, e)

    # Update deployment state
    deployment.state = DeploymentState.DESTROYED
    deployment.url = None

    if deployment.is_latest:
        deployment.is_latest = False
        # Flush the is_latest=False change to the database BEFORE promoting
        # the previous deployment. Without this, SQLAlchemy may flush the
        # SET is_latest=True on the previous deployment first, violating the
        # ix_deployments_unique_active_name partial unique constraint.
        db.flush()

        # Find the most recent READY deployment with the same name to promote
        # This ensures the redeployment chain stays intact
        previous_ready = (
            db.query(Deployment)
            .filter(
                Deployment.tenant_id == auth.tenant_id,
                Deployment.name == deployment.name,
                Deployment.state == DeploymentState.READY,
                Deployment.deployment_id != deployment.deployment_id,
            )
            .order_by(Deployment.created_at.desc())
            .first()
        )
        if previous_ready:
            previous_ready.is_latest = True
            logger.info(
                "Promoted %s to is_latest after destroying %s",
                previous_ready.deployment_id,
                deployment_id,
            )

    db.commit()

    return DestroyResponse(
        deployment_id=deployment_id,
        status="destroyed",
        message="Deployment destroyed successfully"
        if provider_destroyed
        else "Deployment marked as destroyed",
    )
