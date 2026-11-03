"""Infrastructure failures recognised from GitLab's own failure reason, without an LLM call.

GitLab gives every failed job a `failure_reason`. Some values say that the runner or
GitLab failed, not the job's script: no change to the repository can fix them. When
every failed job has such a reason, the triage result is known without asking the LLM.
Any other case (e.g. `script_failure`, or no reason) goes to the triage agent as before.
"""

from __future__ import annotations

from typing import Any

from agents.triage import TriageOutput

# Reasons set by GitLab when the runner or the platform failed, not the job's script
INFRA_FAILURE_REASONS = frozenset(
    {
        "runner_system_failure",
        "stuck_or_timeout_failure",
        "job_execution_timeout",
        "scheduler_failure",
        "api_failure",
        "data_integrity_failure",
    }
)


def infra_triage(jobs: list[dict[str, Any]]) -> TriageOutput | None:
    """Return an infra_flaky triage when every failed job failed for an infrastructure reason, else None."""
    if not jobs or not all(job.get("failure_reason") in INFRA_FAILURE_REASONS for job in jobs):
        return None
    reasons = "; ".join(f'job {job.get("id")} "{job.get("name")}": {job.get("failure_reason")}' for job in jobs)
    return TriageOutput(
        summary=f"GitLab reports an infrastructure failure for every failed job ({reasons}).",
        category="infra_flaky",
        failed_job_id=int(jobs[0]["id"]),
        log_excerpt=f"failure_reason given by GitLab: {reasons}"[:2000],
        fixable_by_code=False,
    )
