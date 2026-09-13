"""Reconcile deployment rows whose worker job no longer exists.

Why this exists
---------------
A deployment row moves QUEUED -> BUILDING -> DEPLOYING -> READY/FAILED only from
inside the RQ job that processes it. If that job dies without running its
failure path — the worker machine is replaced by a platform deploy, OOM-killed,
or Fly stops it past ``kill_timeout`` — the row is left in BUILDING forever with
no error message. Nothing else ever touches it. Seen in prod on 2026-09-11
(customer deployment orphaned by a hub release) and reproduced on 2026-09-13 by
restarting the worker machine mid-build: RQ still listed the dead worker as
busy, the row stayed BUILDING with a frozen ``updated_at``.

What this does
--------------
Every worker runs :func:`reconcile_orphaned_deployments` at startup and then
every :data:`RECONCILE_INTERVAL_SECONDS`. A row in an in-flight state is an
orphan when no live RQ job exists for it: not queued, and not held by a worker
whose heartbeat is fresh. Orphans are marked FAILED with a message that tells
the customer what happened and that nothing was changed on their app.

Two guards keep this from ever failing a healthy build:

* ``settle_seconds``: rows updated very recently are skipped, so a job that is
  between "dequeued" and "registered as started" is never judged.
* ``heartbeat_grace_seconds``: a worker is only considered dead once its
  heartbeat is well past RQ's own monitoring interval (30 s). RQ also expires
  dead worker keys after ``DEFAULT_WORKER_TTL`` (420 s), after which the worker
  simply isn't listed.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

from rq import Queue, Worker
from rq.job import Job

from runtm_shared.types import DeploymentState

logger = logging.getLogger(__name__)

IN_FLIGHT_STATES = (DeploymentState.QUEUED, DeploymentState.BUILDING, DeploymentState.DEPLOYING)

RECONCILE_INTERVAL_SECONDS = 5 * 60
SETTLE_SECONDS = 3 * 60
HEARTBEAT_GRACE_SECONDS = 5 * 60

ORPHAN_ERROR_MESSAGE = (
    "Build interrupted: the build worker was replaced while this deployment was in "
    "flight (usually a platform deploy) and the job was lost before it could finish. "
    "Your app was not changed.\n"
    "Recovery: redeploy to start a fresh build."
)


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _job_deployment_id(job: Job | None) -> str | None:
    """process_deployment(deployment_id, redeploy_from=None, ...) — first positional arg."""
    if job is None or not job.args:
        return None
    dep = job.args[0]
    return dep if isinstance(dep, str) else None


def live_worker_names(
    workers: Iterable[Worker], now: datetime, heartbeat_grace_seconds: int = HEARTBEAT_GRACE_SECONDS
) -> set[str]:
    """Workers whose heartbeat is fresh enough to still own a job."""
    live: set[str] = set()
    for w in workers:
        hb = _as_utc(getattr(w, "last_heartbeat", None))
        if hb is None:
            # No heartbeat recorded yet (just born): give it the benefit of the doubt.
            live.add(w.name)
        elif now - hb <= timedelta(seconds=heartbeat_grace_seconds):
            live.add(w.name)
    return live


def live_deployment_ids(
    queue: Queue,
    now: datetime | None = None,
    heartbeat_grace_seconds: int = HEARTBEAT_GRACE_SECONDS,
) -> set[str]:
    """Deployment ids that still have a live job: queued, or started on a live worker."""
    now = now or datetime.now(timezone.utc)
    live: set[str] = set()

    for job in queue.jobs:  # still waiting to be picked up
        dep = _job_deployment_id(job)
        if dep:
            live.add(dep)

    workers = live_worker_names(
        Worker.all(connection=queue.connection), now, heartbeat_grace_seconds
    )
    started_ids = queue.started_job_registry.get_job_ids()
    for job in Job.fetch_many(started_ids, connection=queue.connection):
        dep = _job_deployment_id(job)
        if dep and getattr(job, "worker_name", None) in workers:
            live.add(dep)
    return live


def find_orphans(
    rows: Iterable[Any], live_ids: set[str], now: datetime, settle_seconds: int = SETTLE_SECONDS
) -> list[Any]:
    """Pure: in-flight rows with no live job that are past the settle window."""
    cutoff = now - timedelta(seconds=settle_seconds)
    orphans = []
    for row in rows:
        if row.state not in IN_FLIGHT_STATES:
            continue
        if row.deployment_id in live_ids:
            continue
        updated = _as_utc(row.updated_at) or _as_utc(row.created_at)
        if updated is not None and updated > cutoff:
            continue
        orphans.append(row)
    return orphans


def _load_in_flight_rows(db: Any) -> list[Any]:
    """All deployments in an in-flight state. runtm_api is imported lazily: the worker
    has it on its path in the image (jobs/deploy.py relies on the same), CI's worker
    job does not."""
    from runtm_api.db.models import Deployment

    return db.query(Deployment).filter(Deployment.state.in_(IN_FLIGHT_STATES)).all()


def _create_session() -> Any:
    from runtm_api.db import create_session

    return create_session()


def reconcile_orphaned_deployments(db: Any, queue: Queue, now: datetime | None = None) -> list[str]:
    """Mark orphaned in-flight deployments FAILED. Returns the deployment ids it failed."""
    now = now or datetime.now(timezone.utc)
    rows = _load_in_flight_rows(db)
    if not rows:
        return []
    live = live_deployment_ids(queue, now)
    orphans = find_orphans(rows, live, now)
    failed: list[str] = []
    for row in orphans:
        logger.warning(
            "reconcile: %s (%s, v%s) has no live job — marking FAILED (was %s since %s)",
            row.deployment_id,
            row.name,
            row.version,
            row.state.value,
            row.updated_at,
        )
        row.state = DeploymentState.FAILED
        row.error_message = ORPHAN_ERROR_MESSAGE
        row.updated_at = now
        failed.append(row.deployment_id)
    if failed:
        db.commit()
    return failed


def run_reconcile_once(redis_conn: Any) -> list[str]:
    """One reconciliation pass with its own DB session. Never raises."""
    db = _create_session()
    try:
        return reconcile_orphaned_deployments(db, Queue("deployments", connection=redis_conn))
    except Exception:  # noqa: BLE001 — a reconciler bug must never take the worker down
        logger.exception("reconcile: pass failed")
        try:
            db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return []
    finally:
        db.close()


def start_reconciler(
    redis_conn: Any, interval_seconds: int = RECONCILE_INTERVAL_SECONDS
) -> threading.Thread:
    """Run a pass now, then every ``interval_seconds`` in a daemon thread."""

    def loop() -> None:
        while True:
            failed = run_reconcile_once(redis_conn)
            if failed:
                print(
                    f"reconcile: marked {len(failed)} orphaned deployment(s) FAILED: {', '.join(failed)}"
                )
            time.sleep(interval_seconds)

    t = threading.Thread(target=loop, name="deployment-reconciler", daemon=True)
    t.start()
    return t
