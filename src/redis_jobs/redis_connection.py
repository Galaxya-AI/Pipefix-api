"""Redis connection used by the run queue, the workers and the run statuses."""

from __future__ import annotations

import os

from redis import Redis

# E.g. redis://redis:6379/0 (empty: no queue, runs in API threads)
REDIS_URL = os.getenv("REDIS_URL", "")


def connect_redis(url: str = REDIS_URL) -> Redis:
    """Return a Redis client after checking that Redis answers.

    Raise ValueError without an address, redis.exceptions.ConnectionError if Redis does not answer.
    """
    if not url:
        raise ValueError("REDIS_URL is missing in .env.")
    client = Redis.from_url(url)
    # Fail at start-up rather than at the first run.
    client.ping()
    return client
