"""Jobs run by the workers: one remediation per job.

A worker takes the run from the queue, waits for the lock of its branch,
runs the remediation workflow and records the outcome. The services (GitLab
client, LLM client, agents, tools) are built for each job and closed after it.
"""

from __future__ import annotations

from collections.abc import Callable

from redis import Redis

from app_runs.launcher import RunFunction
from app_runs.run_store import RunStore
from journal.run_journal import RunJournal
from redis_jobs.branch_lock import BranchLock
from redis_jobs.redis_connection import connect_redis
from redis_jobs.status_backend import RedisStatusBackend
from startup.services import build_job_services

# Builds the services of one job: (function that runs a remediation, function that releases the resources)
RunFactory = Callable[[], tuple[RunFunction, Callable[[], None]]]


def run_job(
    redis: Redis,
    run_id: str,
    project: str,
    pipeline_id: int,
    branch: str,
    dry_run: bool,
    run_factory: RunFactory,
) -> str:
    """Run one queued remediation under the lock of its branch and return its final status.

    A crash is raised again so the queue keeps the job in its failed registry.
    """
    store = RunStore(statuses=RedisStatusBackend(redis))
    lock = BranchLock(redis, project, branch)
    if not lock.acquire():
        store.fail(run_id, f"Branch {branch} is busy: another run did not finish in time.")
        return "error"
    try:
        store.mark_running(run_id)
        run_function, close = run_factory()
        try:
            report = run_function(project, pipeline_id, dry_run, RunJournal(run_id=run_id))
        finally:
            close()
        # The report is on disk now.
        store.finish(run_id)
        return str(report.get("status", ""))
    except Exception as error:
        store.fail(run_id, f"{type(error).__name__}: {error}")
        raise
    finally:
        lock.release()


def execute_run(run_id: str, project: str, pipeline_id: int, branch: str, dry_run: bool) -> str:
    """Entry point called by the workers for each queued run; returns its final status."""
    return run_job(connect_redis(), run_id, project, pipeline_id, branch, dry_run, build_job_services)
