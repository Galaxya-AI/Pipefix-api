"""Branch lock: only one run at a time on the same project and branch.

Two runs on the same branch would create fix branches and merge requests
that conflict. The lock lives in Redis, so it is shared by every worker; without
Redis (runs in API threads) there is no branch lock.

A job waits at most RUN_TIMEOUT_S for its branch, then runs at most RUN_TIMEOUT_S:
RQ stops it after JOB_TIMEOUT_S. The lock lives longer than that, so it can never
expire while its job still runs. A worker killed without releasing it blocks the
branch until the lock expires (about JOB_TIMEOUT_S).
"""

from __future__ import annotations

import os

from redis import Redis

from redis_jobs.status_backend import REDIS_KEY_PREFIX

# Maximum duration of one run in seconds, and maximum wait for its branch.
RUN_TIMEOUT_S = int(os.getenv("RUN_TIMEOUT_S", "1800"))

# Wait for the branch, then the run: RQ stops the job after this.
JOB_TIMEOUT_S = 2 * RUN_TIMEOUT_S

# The lock outlives the longest job, with a margin.
LOCK_LIFETIME_S = JOB_TIMEOUT_S + 60


class BranchLock:
    """Redis lock of one project and branch, held at most lifetime_s, waited for at most wait_s."""

    def __init__(
        self,
        redis: Redis,
        project: str,
        branch: str,
        wait_s: int = RUN_TIMEOUT_S,
        lifetime_s: int = LOCK_LIFETIME_S,
    ) -> None:
        self.lock = redis.lock(
            f"{REDIS_KEY_PREFIX}:lock:{project}:{branch}", timeout=lifetime_s, blocking_timeout=wait_s
        )

    def acquire(self) -> bool:
        """Take the lock, waiting while another run holds it; False after the maximum wait."""
        return bool(self.lock.acquire())

    def release(self) -> None:
        # The lock may have expired, or been taken by another run since.
        if self.lock.owned():
            self.lock.release()
