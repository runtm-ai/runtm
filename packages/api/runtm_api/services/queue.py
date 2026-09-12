"""Redis queue service for enqueueing deployment jobs."""

from __future__ import annotations

import logging

from redis import Redis
from rq import Queue

from runtm_shared.types import Limits

logger = logging.getLogger(__name__)


def job_description(
    deployment_id: str, redeploy_from: str | None = None, config_only: bool = False
) -> str:
    """Log-safe RQ job description: names only, never kwargs (which carry secrets)."""
    text = f"process_deployment {deployment_id}"
    if redeploy_from:
        text += f" (redeploy from {redeploy_from})"
    if config_only:
        text += " [config-only]"
    return text


def enqueue_deployment(
    deployment_id: str,
    redis_url: str,
    redeploy_from: str | None = None,
    secrets: dict[str, str] | None = None,
    config_only: bool = False,
) -> str | None:
    """Enqueue a deployment job to the worker queue.

    Args:
        deployment_id: Deployment ID to process
        redis_url: Redis connection URL
        redeploy_from: If this is a redeployment, the previous deployment ID
                      to get existing infrastructure from
        secrets: Secrets to inject to the deployment provider (passed through,
                never stored in Runtm DB)
        config_only: If True, skip Docker build and reuse previous image
                    (for config-only changes like env vars or tier)

    Returns:
        Job ID if successfully enqueued, None otherwise

    Security note:
        Secrets are passed through the job queue to the worker. They are
        encrypted in transit via Redis TLS (if configured) and are only
        held in memory. They are NEVER persisted to the Runtm database.
    """
    try:
        redis_conn = Redis.from_url(redis_url)
        queue = Queue("deployments", connection=redis_conn)

        # Enqueue the job - worker will import and run this function.
        # RQ's default job description is the call string INCLUDING kwargs, and
        # the worker logs it at start/finish — so an explicit description keeps
        # secret values out of the logs.
        job = queue.enqueue(
            "runtm_worker.jobs.process_deployment",
            deployment_id,
            redeploy_from,  # Pass the previous deployment ID if redeploying
            secrets=secrets,  # Pass secrets (never stored)
            config_only=config_only,  # Skip build and reuse image
            job_timeout=Limits.JOB_TIMEOUT_SECONDS,  # build + deploy + grace; see runtm_shared.types.Limits
            result_ttl=86400,  # Keep result for 24 hours
            description=job_description(deployment_id, redeploy_from, config_only),
        )

        if config_only:
            logger.info(f"Enqueued config-only deployment {deployment_id} as job {job.id}")
        elif redeploy_from:
            logger.info(
                f"Enqueued redeployment {deployment_id} (from {redeploy_from}) as job {job.id}"
            )
        else:
            logger.info(f"Enqueued deployment {deployment_id} as job {job.id}")

        # Log secret names only (never values)
        if secrets:
            logger.info(f"Deployment {deployment_id} has {len(secrets)} secrets to inject")

        return job.id

    except Exception as e:
        logger.error(f"Failed to enqueue deployment {deployment_id}: {e}")
        return None
