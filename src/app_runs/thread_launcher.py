"""Thread launcher: starts a remediation in a thread of the API process (development).

The HTTP request does not wait for the agents (a run can take minutes): the
client gets the run id and follows it with GET /api/v1/runs/<run_id>.
There is no queue: every run starts at once. In production, use the Redis
queue (redis_jobs.queue_launcher.QueueLauncher) instead.
"""

from __future__ import annotations

import threading

from app_runs.launcher import RunFunction
from app_runs.run_store import RunStore
from journal.run_journal import RunJournal


class ThreadLauncher:
    """Start runs in background threads of the API process."""

    def __init__(self, run_function: RunFunction, store: RunStore) -> None:
        self.run_function = run_function
        self.store = store
        # Kept so tests can wait for them.
        self.threads: list[threading.Thread] = []

    def launch(self, project: str, pipeline_id: int | None, dry_run: bool) -> str:
        """Start a run and return its id; pipeline_id=None means the latest failed pipeline."""
        # The journal creates the run folder and id.
        journal = RunJournal(self.store.runs_dir)
        self.store.start(journal.run_id, project, pipeline_id, dry_run)
        thread = threading.Thread(target=self.execute, args=(project, pipeline_id, dry_run, journal), daemon=True)
        self.threads.append(thread)
        thread.start()
        return journal.run_id

    def execute(self, project: str, pipeline_id: int | None, dry_run: bool, journal: RunJournal) -> None:
        # The thread must record any crash: nothing else would see it.
        try:
            self.run_function(project, pipeline_id, dry_run, journal)
        except Exception as error:
            self.store.fail(journal.run_id, f"{type(error).__name__}: {error}")
            journal.log("api", "run_crashed", {"error": str(error)})
            return
        # The report is on disk now.
        self.store.finish(journal.run_id)

    def wait(self, timeout_s: float = 10.0) -> None:
        """Wait for every started thread, at most timeout_s seconds each (used by tests)."""
        for thread in self.threads:
            thread.join(timeout_s)
