"""Pipeline waiter: waits for the pipeline of the fix branch and reads its outcome.

Used only when WAIT_FOR_PIPELINE is true. A failed pipeline starts a new round;
any other outcome ends the run.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from journal.run_journal import RunJournal
from workflow_remediation.ports import CodePlatform

FINAL_STATUSES = frozenset({"success", "failed", "canceled", "skipped"})


def outcome_status(finished: dict[str, Any] | None) -> str | None:
    """Return the final status of the run, or None when the pipeline failed again and a new round must start."""
    if finished is None:
        return "pipeline_timeout"
    if finished.get("status") == "success":
        return "pipeline_fixed"
    if finished.get("status") == "failed":
        return None
    return f"pipeline_{finished.get('status')}"


class PipelineWaiter:
    def __init__(
        self, platform: CodePlatform, journal: RunJournal, sleep: Callable[[float], None] = time.sleep
    ) -> None:
        self.platform = platform
        self.journal = journal
        self.sleep = sleep

    def wait(self, project: str, branch: str, sha: str, timeout_s: int, poll_s: float = 20.0) -> dict[str, Any] | None:
        """Return the finished pipeline of the commit, or None after the timeout."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            pipelines = self.platform.list_pipelines(project, ref=branch, sha=sha, per_page=1)
            if (
                pipelines
                and str(pipelines[0].get("sha", "")) == sha
                and pipelines[0].get("status") in FINAL_STATUSES
            ):
                details = {"id": pipelines[0].get("id"), "status": pipelines[0].get("status")}
                self.journal.log("pipeline_waiter", "pipeline_finished", details)
                return pipelines[0]
            self.sleep(poll_s)
        self.journal.log("pipeline_waiter", "pipeline_timeout", {"branch": branch})
        return None
