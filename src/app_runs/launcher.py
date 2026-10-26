"""How the API starts a run, whatever runs it.

Two implementations:
- app_runs.thread_launcher.ThreadLauncher: a thread of the API process (development);
- redis_jobs.queue_launcher.QueueLauncher: a Redis queue read by workers (production).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from journal.run_journal import RunJournal

# (project, pipeline_id, dry_run, journal) -> report
RunFunction = Callable[[str, "int | None", bool, RunJournal], dict[str, Any]]


class LaunchError(Exception):
    """A run cannot be started (e.g. no failed pipeline to process)."""


class Launcher(Protocol):
    """Starts runs and returns their id at once; the run goes on in the background."""

    def launch(self, project: str, pipeline_id: int | None, dry_run: bool) -> str: ...
