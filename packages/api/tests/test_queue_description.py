"""The deployment job's RQ description must never contain kwargs.

RQ builds the default description from the call string *including kwargs*, and
the worker logs that description when a job starts and finishes. With secrets
passed as a kwarg, every deploy that ships a secret would print its value into
the worker logs. enqueue_deployment therefore sets an explicit description.
"""

from unittest.mock import MagicMock, patch

from rq.utils import get_call_string

from runtm_api.services.queue import enqueue_deployment, job_description

SECRETS = {"DATABASE_URL": "postgres://user:S3CRET@db.example/app"}


def test_rq_default_description_would_leak_the_secret_value():
    # The premise: RQ's own call string renders kwargs verbatim.
    rendered = get_call_string(
        "runtm_worker.jobs.process_deployment",
        ("dep_1", None),
        {"secrets": SECRETS, "config_only": False},
        max_length=75,
    )
    assert "S3CRET" in rendered


def test_enqueue_sets_a_description_without_kwargs():
    fake_queue = MagicMock()
    fake_queue.enqueue.return_value = MagicMock(id="job-1")
    with (
        patch("runtm_api.services.queue.Redis") as redis_cls,
        patch("runtm_api.services.queue.Queue", return_value=fake_queue),
    ):
        redis_cls.from_url.return_value = MagicMock()
        job_id = enqueue_deployment("dep_1", "redis://x", redeploy_from="dep_0", secrets=SECRETS)

    assert job_id == "job-1"
    kwargs = fake_queue.enqueue.call_args.kwargs
    assert kwargs["secrets"] == SECRETS  # still delivered to the worker
    assert kwargs["description"] == "process_deployment dep_1 (redeploy from dep_0)"
    assert "S3CRET" not in kwargs["description"]


def test_job_description_variants():
    assert job_description("dep_1") == "process_deployment dep_1"
    assert job_description("dep_1", config_only=True) == "process_deployment dep_1 [config-only]"
    assert (
        job_description("dep_1", "dep_0", True)
        == "process_deployment dep_1 (redeploy from dep_0) [config-only]"
    )
