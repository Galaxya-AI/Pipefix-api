"""Remediation round: runs the agents once on a failed pipeline, until a fix is approved or refused.

Order of one round (rules in workflow_remediation.transitions):
1. triage: stop for a transient infrastructure failure or a failure code cannot fix;
2. diagnosis: root cause and fix plan;
3. fixer <-> reviewer: up to MAX_FIX_ATTEMPTS attempts, the issues of a
   rejected review are given to the next attempt.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any

from agent_errors.errors import AgentRunError
from agent_runner.agent_runner import AgentRunner
from agents.outputs import AgentOutput
from agents.reviewer import ReviewOutput
from agents.triage import TriageOutput
from changeset.changeset import Changeset
from journal.run_journal import RunJournal
from policy.data_policy import DataPolicy
from text_tools.log_cleaning import clean_log
from tools.tool import Tool
from workflow_analysis.task_messages import diagnosis_task, fix_task, pipeline_context, review_task
from workflow_analysis.toolbox import build_run_registry
from workflow_remediation.ports import CodePlatform
from workflow_remediation.round_result import RoundResult
from workflow_remediation.transitions import STATUS_INFRA_FLAKY, STATUS_NOT_FIXABLE, STEP_PUBLISH, next_step

MAX_FIX_ATTEMPTS = int(os.getenv("MAX_FIX_ATTEMPTS", "2"))

AUTO_RETRY_FLAKY = os.getenv("AUTO_RETRY_FLAKY", "false").strip().lower() in ("1", "true", "yes")

# Log lines of the first failed job given to every agent
CONTEXT_LOG_LINES = int(os.getenv("CONTEXT_LOG_LINES", "150"))


class RemediationRound:
    """Run the agents once on a failed pipeline.

    The runner is shared by every round, so the LLM usage adds up; dry_run forbids
    any change on the platform (no job retry).
    """

    def __init__(
        self,
        platform: CodePlatform,
        runner: AgentRunner,
        tools: Iterable[Tool],
        policy: DataPolicy,
        journal: RunJournal,
        dry_run: bool,
        max_fix_attempts: int = MAX_FIX_ATTEMPTS,
        auto_retry_flaky: bool = AUTO_RETRY_FLAKY,
    ) -> None:
        self.platform = platform
        self.runner = runner
        self.tools = list(tools)
        self.policy = policy
        self.journal = journal
        self.dry_run = dry_run
        self.max_fix_attempts = max(1, max_fix_attempts)
        self.auto_retry_flaky = auto_retry_flaky

    def failed_jobs(self, project: str, pipeline: dict[str, Any]) -> list[dict[str, Any]]:
        """Return the failed jobs that block the pipeline, or every failed job if none blocks it."""
        jobs = self.platform.pipeline_jobs(project, int(pipeline["id"]), scope="failed")
        return [job for job in jobs if not job.get("allow_failure")] or jobs

    def run(self, project: str, pipeline: dict[str, Any], allow_research: bool) -> RoundResult:
        result = RoundResult(pipeline_id=int(pipeline["id"]))
        jobs = self.failed_jobs(project, pipeline)
        if not jobs:
            result.status = "no_failed_jobs"
            return result
        sha = str(pipeline.get("sha", ""))
        # Files are read from the analysed commit, not from the branch head
        changeset = Changeset(lambda path: self.platform.file_raw(project, path, sha))
        self.runner.tools = build_run_registry(
            self.tools, project, sha, changeset, self.policy, self.journal, allow_research
        )
        first_log = clean_log(self.platform.job_trace(project, int(jobs[0]["id"])), CONTEXT_LOG_LINES)
        context = pipeline_context(project, pipeline, jobs, first_log)
        if not self.analyse(project, context, result):
            return result
        self.fix_and_review(context, changeset, result)
        return result

    def analyse(self, project: str, context: str, result: RoundResult) -> bool:
        """Run the triage, then the diagnosis if code can fix the failure; True if a diagnosis is ready."""
        result.triage = self.run_agent("triage", context, result)
        if result.triage is None:
            return False
        step = next_step(result.triage)
        if step == STATUS_INFRA_FLAKY:
            result.status = self.handle_flaky(project, result.triage)
            return False
        if step == STATUS_NOT_FIXABLE:
            result.status = STATUS_NOT_FIXABLE
            return False
        result.diagnosis = self.run_agent("diagnosis", diagnosis_task(context, result.triage), result)
        return result.diagnosis is not None

    def fix_and_review(self, context: str, changeset: Changeset, result: RoundResult) -> None:
        """Alternate fixer and reviewer until the fix is approved or the attempts run out."""
        diagnosis = result.diagnosis
        if diagnosis is None:
            return
        issues: list[str] = []
        for attempt in range(1, self.max_fix_attempts + 1):
            result.attempts = attempt
            result.fix = self.run_agent("fixer", fix_task(context, diagnosis, issues), result)
            if result.fix is None:
                return
            if changeset.is_empty():
                result.status = "no_change_proposed"
                return
            result.diff = changeset.diff()
            result.review = self.run_agent("reviewer", review_task(context, diagnosis, result.fix, result.diff), result)
            if result.review is None:
                return
            if next_step(result.review) == STEP_PUBLISH:
                result.status = "fix_ready"
                result.files = changeset.changed_files()
                return
            issues = list(result.review.issues) if isinstance(result.review, ReviewOutput) else []
        result.status = "review_rejected"

    def run_agent(self, agent_name: str, task: str, result: RoundResult) -> AgentOutput | None:
        """Return the agent's validated output, or None after recording its failure in the round."""
        try:
            return self.runner.run(agent_name, task)
        except AgentRunError as error:
            result.status = f"{agent_name}_failed"
            result.error = str(error)
            return None

    def handle_flaky(self, project: str, triage: AgentOutput) -> str:
        """Retry the failed job if allowed (never in dry-run mode)."""
        if not self.auto_retry_flaky or self.dry_run or not isinstance(triage, TriageOutput):
            return STATUS_INFRA_FLAKY
        self.platform.retry_job(project, triage.failed_job_id)
        self.journal.log("remediation_round", "job_retried", {"job": triage.failed_job_id})
        return "infra_flaky_job_retried"
