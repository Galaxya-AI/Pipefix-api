"""Run store: status of the runs not yet finished, and reading of the finished ones from disk.

Each run writes its folder runs/<run_id>/ (journal.jsonl, report.json,
fix.patch). The store reads these files for the API. The status of the
queued, running or crashed runs is kept in a StatusBackend (memory in
development, Redis in production).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from journal.run_journal import JOURNAL_FILE_NAME, REPORT_FILE_NAME, RUNS_DIR
from redis_jobs.status_backend import FAILED_STATUS_TTL_S, MemoryStatusBackend, StatusBackend

# Letters, digits, "-" and "_" only: a run id must never allow path traversal.
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,80}$")

PATCH_FILE_NAME = "fix.patch"


class RunStore:
    """Status and files of the runs.

    runs_dir is shared by the API and the workers; statuses=None keeps the statuses in memory.
    """

    def __init__(self, runs_dir: str | Path = RUNS_DIR, statuses: StatusBackend | None = None) -> None:
        self.runs_dir = Path(runs_dir)
        self.statuses: StatusBackend = statuses if statuses is not None else MemoryStatusBackend()

    def folder(self, run_id: str) -> Path | None:
        """Return the folder of a run, or None for an invalid id."""
        if not RUN_ID_PATTERN.match(run_id):
            return None
        return self.runs_dir / run_id

    def start(
        self, run_id: str, project: str, pipeline_id: int | None, dry_run: bool, status: str = "running", **extra: Any
    ) -> None:
        """Record a run that is "queued" (waits in a queue) or "running"; extra fields are shown as is."""
        self.statuses.set(
            run_id,
            {
                "run_id": run_id,
                "project": project,
                "pipeline_id": pipeline_id,
                "dry_run": dry_run,
                "status": status,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                **extra,
            },
        )

    def mark_running(self, run_id: str) -> None:
        """Mark a queued run as running (called by the worker that takes it)."""
        entry = self.statuses.get(run_id) or {"run_id": run_id}
        entry["status"] = "running"
        entry["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.statuses.set(run_id, entry)

    def finish(self, run_id: str) -> None:
        """Forget a finished run: its report is now on disk."""
        self.statuses.delete(run_id)

    def fail(self, run_id: str, error: str) -> None:
        """Record a run that crashed before writing its report (kept 7 days)."""
        entry = self.statuses.get(run_id) or {"run_id": run_id}
        entry["status"] = "error"
        entry["error"] = error
        self.statuses.set(run_id, entry, FAILED_STATUS_TTL_S)

    def read_report(self, run_id: str) -> dict[str, Any] | None:
        folder = self.folder(run_id)
        if folder is None or not (folder / REPORT_FILE_NAME).is_file():
            return None
        report: dict[str, Any] = json.loads((folder / REPORT_FILE_NAME).read_text(encoding="utf-8"))
        return report

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Return the status of a run not yet finished, else its report on disk."""
        entry = self.statuses.get(run_id)
        if entry is not None:
            return entry
        return self.read_report(run_id)

    def list_runs(self) -> list[dict[str, Any]]:
        """Return the id, project, status and pipeline of every run, newest first."""
        summaries: dict[str, dict[str, Any]] = {}
        for folder in self.runs_dir.glob("*/"):
            report = self.read_report(folder.name)
            if report is not None:
                summaries[folder.name] = {
                    "run_id": folder.name,
                    "project": report.get("project"),
                    "status": report.get("status"),
                    "pipeline": report.get("pipeline"),
                }
        # Runs not finished win over the disk.
        summaries.update(self.statuses.all())
        # Ids start with the date, so sorting them puts the newest first.
        return [summaries[run_id] for run_id in sorted(summaries, reverse=True)]

    def events(self, run_id: str) -> list[dict[str, Any]] | None:
        """Return the journal events of a run in order, or None if it has no journal."""
        folder = self.folder(run_id)
        if folder is None or not (folder / JOURNAL_FILE_NAME).is_file():
            return None
        lines = (folder / JOURNAL_FILE_NAME).read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if line.strip()]

    def patch(self, run_id: str) -> str | None:
        """Return the unified diff of a dry run, or None if it has no patch."""
        folder = self.folder(run_id)
        if folder is None or not (folder / PATCH_FILE_NAME).is_file():
            return None
        return (folder / PATCH_FILE_NAME).read_text(encoding="utf-8")
