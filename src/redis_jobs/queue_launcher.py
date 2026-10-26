"""Queue launcher: puts runs in a Redis queue, read by the workers (production).

For each request it:
1. finds the pipeline to process (the latest failed one when no id is given);
2. records the status "queued", visible with GET /api/v1/runs/<run_id>;
3. refuses a duplicate: the same project, pipeline and mode already queued or
   processed returns the existing run id;
4. puts the run in the "high" queue (important branches) or the "default" queue.

If the run cannot be queued, its deduplication key is released, so the pipeline
is not blocked for DEDUP_TTL_S.
"""

from __future__ import annotations

import os

from redis import Redis
from rq import Queue
from rq.job import Job

from app_runs.launcher import LaunchError
from app_runs.run_store import RunStore
from journal.run_journal import new_run_id
from redis_jobs.branch_lock import JOB_TIMEOUT_S
from redis_jobs.priority import queue_for_branch
from redis_jobs.status_backend import FAILED_STATUS_TTL_S, REDIS_KEY_PREFIX
from workflow_analysis.pipeline_selection import find_pipeline
from workflow_remediation.ports import CodePlatform

# Time during which the same pipeline is not processed again, in seconds (1 day)
DEDUP_TTL_S = int(os.getenv("DEDUP_TTL_S", "86400"))

JOB_FUNCTION = "redis_jobs.jobs.execute_run"

# Deletes the key only if it still holds this run id; atomic in Redis.
RELEASE_KEY_SCRIPT = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end return 0"


def dedup_key(project: str, pipeline_id: int, dry_run: bool) -> str:
    """Return the Redis key that marks a pipeline as already queued.

    The mode is part of the key: a dry run does not prevent a later run that publishes.
    """
    mode = "dry_run" if dry_run else "apply"
    return f"{REDIS_KEY_PREFIX}:dedup:{project}:{pipeline_id}:{mode}"


class QueueLauncher:
    """Put runs in the Redis queues; the platform client finds the pipeline and its branch."""

    def __init__(
        self,
        redis: Redis,
        store: RunStore,
        platform: CodePlatform,
        dedup_ttl_s: int = DEDUP_TTL_S,
        job_timeout_s: int = JOB_TIMEOUT_S,
    ) -> None:
        self.redis = redis
        self.store = store
        self.platform = platform
        self.dedup_ttl_s = dedup_ttl_s
        self.job_timeout_s = job_timeout_s

    def launch(self, project: str, pipeline_id: int | None, dry_run: bool) -> str:
        """Queue a run and return its id, or the id of the same run already queued.

        pipeline_id=None means the latest failed pipeline; raise LaunchError if there is none.
        """
        pipeline = find_pipeline(self.platform, project, pipeline_id)
        if pipeline is None:
            raise LaunchError(f"No failed pipeline to process for project {project}.")
        found_id = int(pipeline["id"])
        branch = str(pipeline.get("ref", ""))
        run_id = new_run_id()
        key = dedup_key(project, found_id, dry_run)
        queue_name = queue_for_branch(self.platform, project, branch)
        # Recorded before the key: a key whose run has no status was left by a crash.
        self.store.start(run_id, project, found_id, dry_run, status="queued", branch=branch, queue=queue_name)
        holder = self.claim(key, run_id)
        if holder != run_id:
            self.store.statuses.delete(run_id)
            return holder
        try:
            # The job id is the run id.
            Queue(queue_name, connection=self.redis).enqueue(
                JOB_FUNCTION,
                run_id,
                project,
                found_id,
                branch,
                dry_run,
                job_id=run_id,
                job_timeout=self.job_timeout_s,
                failure_ttl=FAILED_STATUS_TTL_S,
            )
        except Exception as error:
            # The connection may break after RQ stored the job: it is queued then, and must keep its key.
            if Job.exists(run_id, connection=self.redis):
                return run_id
            self.redis.eval(RELEASE_KEY_SCRIPT, 1, key, run_id)
            self.store.fail(run_id, f"Not queued: {error}")
            raise
        return run_id

    def claim(self, key: str, run_id: str) -> str:
        """Take the deduplication key for run_id; return run_id, or the id of the run that holds the key."""
        for _ in range(2):
            if self.redis.set(key, run_id, nx=True, ex=self.dedup_ttl_s):
                return run_id
            value = self.redis.get(key)
            if value is None:
                continue
            holder = value.decode() if isinstance(value, bytes) else str(value)
            if self.store.get_run(holder) is not None:
                return holder
            # No status and no report: the key was left by a crash, it must not block the pipeline.
            self.redis.eval(RELEASE_KEY_SCRIPT, 1, key, holder)
        raise LaunchError("This pipeline is being queued by another request, try again.")
