"""Worker entrypoint - processes deployment jobs from Redis queue."""

from __future__ import annotations

import atexit
import os
import uuid

# Ensure .env is loaded from project root before reading environment
from runtm_shared.env import ensure_env_loaded  # noqa: F401

ensure_env_loaded()

from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from rq import Queue, Worker

from runtm_worker.jobs import process_deployment
from runtm_worker.telemetry import init_telemetry, shutdown_telemetry


def get_redis_connection() -> Redis:
    """Get Redis connection from environment.

    Configures socket timeouts and retry to handle flaky Upstash/Fly 6PN
    connections where the first attempt often gets "Connection closed by server".
    """
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    return Redis.from_url(
        redis_url,
        socket_connect_timeout=30,
        socket_timeout=30,
        socket_keepalive=True,
        retry_on_timeout=True,
        retry_on_error=[RedisConnectionError],
        health_check_interval=30,
    )


def create_queue(redis_conn: Redis | None = None) -> Queue:
    """Create the deployment queue.

    Args:
        redis_conn: Optional Redis connection

    Returns:
        RQ Queue instance
    """
    if redis_conn is None:
        redis_conn = get_redis_connection()
    return Queue("deployments", connection=redis_conn)


def enqueue_deployment(deployment_id: str) -> str:
    """Enqueue a deployment job.

    Args:
        deployment_id: Deployment ID to process

    Returns:
        Job ID
    """
    queue = create_queue()
    job = queue.enqueue(
        process_deployment,
        deployment_id,
        job_timeout="20m",  # 20 minutes max
        result_ttl=86400,  # Keep result for 24 hours
    )
    return job.id


def _warm_redis(conn: Redis, max_attempts: int = 5) -> None:
    """Warm up Redis connection with retries.

    Fly Upstash Redis over 6PN often drops the first connection attempt
    with "Connection closed by server". Warming up ensures the connection
    is stable before RQ tries register_birth() with a MULTI/EXEC transaction.
    """
    import time

    for attempt in range(1, max_attempts + 1):
        try:
            conn.ping()
            print(f"Redis connection ready (attempt {attempt})")
            return
        except Exception as e:
            print(f"Redis warmup attempt {attempt}/{max_attempts}: {e}")
            if attempt < max_attempts:
                time.sleep(2 * attempt)
    raise RuntimeError(f"Redis not reachable after {max_attempts} attempts")


def run_worker() -> None:
    """Run the worker process.

    This is the main entrypoint for the worker container.
    """
    print("Starting Runtm Worker...")
    print(f"Redis URL: {os.environ.get('REDIS_URL', 'redis://localhost:6379')}")

    # Initialize telemetry
    # Worker runs inside Docker, so API is accessible at http://api:8000
    api_url = os.environ.get("RUNTM_API_URL", "http://api:8000")
    api_token = os.environ.get("RUNTM_API_SECRET", "dev-token")
    init_telemetry(api_url=api_url, api_token=api_token)
    atexit.register(shutdown_telemetry)
    print(f"Telemetry initialized (sending to {api_url})")

    redis_conn = get_redis_connection()
    _warm_redis(redis_conn)
    queue = create_queue(redis_conn)

    # Create and run worker
    worker = Worker(
        [queue],
        connection=redis_conn,
        name=f"runtm-worker-{uuid.uuid4().hex[:8]}",
    )

    print(f"Worker {worker.name} started, listening on queue: {queue.name}")
    worker.work()


if __name__ == "__main__":
    run_worker()
