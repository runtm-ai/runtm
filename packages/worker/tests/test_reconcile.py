"""Orphaned-deployment reconciliation.

Reproduced 2026-09-13 in prod: restart the worker machine mid-build and the row
stays BUILDING forever while RQ still lists the dead worker as busy. These tests
pin the rule that turns such rows into FAILED without ever touching a live build.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from runtm_shared.types import DeploymentState
from runtm_worker import reconcile as r

NOW = datetime(2026, 9, 13, 1, 40, tzinfo=timezone.utc)


def _row(dep_id: str, state: DeploymentState, age_s: int) -> SimpleNamespace:
    return SimpleNamespace(
        deployment_id=dep_id,
        name="app",
        version=1,
        state=state,
        error_message=None,
        created_at=NOW - timedelta(seconds=age_s + 5),
        updated_at=NOW - timedelta(seconds=age_s),
    )


def _job(dep_id: str, worker_name: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(args=(dep_id, None), worker_name=worker_name)


def _worker(name: str, heartbeat_age_s: int | None) -> SimpleNamespace:
    hb = (
        None
        if heartbeat_age_s is None
        else (NOW - timedelta(seconds=heartbeat_age_s)).replace(tzinfo=None)
    )
    return SimpleNamespace(name=name, last_heartbeat=hb)


class TestLiveWorkers:
    def test_fresh_heartbeat_is_live_and_stale_is_dead(self) -> None:
        live = r.live_worker_names(
            [_worker("a", 20), _worker("dead", 900), _worker("newborn", None)], NOW
        )
        assert live == {"a", "newborn"}


class TestLiveDeploymentIds:
    def _queue(self, queued: list, started: list) -> MagicMock:
        q = MagicMock()
        q.jobs = queued
        q.started_job_registry.get_job_ids.return_value = [f"job-{i}" for i in range(len(started))]
        q.connection = object()
        return q, started

    def test_queued_jobs_and_jobs_on_live_workers_count_as_live(self) -> None:
        q, started = self._queue(
            queued=[_job("dep_queued")],
            started=[
                _job("dep_live", "w-live"),
                _job("dep_orphan", "w-dead"),
                _job("dep_gone", "w-vanished"),
            ],
        )
        with (
            patch.object(
                r.Worker, "all", return_value=[_worker("w-live", 10), _worker("w-dead", 3600)]
            ),
            patch.object(r.Job, "fetch_many", return_value=started),
        ):
            live = r.live_deployment_ids(q, NOW)
        assert live == {"dep_queued", "dep_live"}


class TestFindOrphans:
    def test_only_stale_in_flight_rows_without_live_job(self) -> None:
        rows = [
            _row("dep_building_orphan", DeploymentState.BUILDING, age_s=600),
            _row("dep_queued_orphan", DeploymentState.QUEUED, age_s=600),
            _row("dep_deploying_live", DeploymentState.DEPLOYING, age_s=600),
            _row("dep_just_started", DeploymentState.BUILDING, age_s=10),  # inside settle window
            _row("dep_ready", DeploymentState.READY, age_s=99999),
            _row("dep_failed", DeploymentState.FAILED, age_s=99999),
        ]
        orphans = r.find_orphans(rows, live_ids={"dep_deploying_live"}, now=NOW)
        assert [o.deployment_id for o in orphans] == ["dep_building_orphan", "dep_queued_orphan"]

    def test_ancient_orphans_from_before_the_fix_are_swept(self) -> None:
        old = _row("dep_c2626cc993bb", DeploymentState.BUILDING, age_s=95 * 24 * 3600)
        assert r.find_orphans([old], live_ids=set(), now=NOW) == [old]


class TestReconcile:
    def test_marks_failed_with_customer_message_and_commits(self) -> None:
        orphan = _row("dep_orphan", DeploymentState.BUILDING, age_s=600)
        live = _row("dep_live", DeploymentState.BUILDING, age_s=600)
        db = MagicMock()
        with (
            patch.object(r, "_load_in_flight_rows", return_value=[orphan, live]),
            patch.object(r, "live_deployment_ids", return_value={"dep_live"}),
        ):
            failed = r.reconcile_orphaned_deployments(db, queue=MagicMock(), now=NOW)
        assert failed == ["dep_orphan"]
        assert orphan.state == DeploymentState.FAILED
        assert orphan.error_message.startswith("Build interrupted: the build worker was replaced")
        assert "Your app was not changed" in orphan.error_message
        assert orphan.updated_at == NOW
        assert live.state == DeploymentState.BUILDING
        db.commit.assert_called_once()

    def test_nothing_in_flight_means_no_redis_calls_and_no_commit(self) -> None:
        db = MagicMock()
        with (
            patch.object(r, "_load_in_flight_rows", return_value=[]),
            patch.object(r, "live_deployment_ids") as live,
        ):
            assert r.reconcile_orphaned_deployments(db, queue=MagicMock(), now=NOW) == []
        live.assert_not_called()
        db.commit.assert_not_called()

    def test_run_once_never_raises(self) -> None:
        with (
            patch.object(r, "_create_session", return_value=MagicMock()),
            patch.object(
                r, "reconcile_orphaned_deployments", side_effect=RuntimeError("redis down")
            ),
        ):
            assert r.run_reconcile_once(redis_conn=MagicMock()) == []
