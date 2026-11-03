"""Remediation round: runs the agents once on a failed pipeline, until a fix is approved or refused.

Order of one round (rules in workflow_remediation.transitions):
1. triage: stop for a transient infrastructure failure or a failure code cannot fix;
2. diagnosis: root cause and fix plan;
3. fixer <-> reviewer: up to MAX_FIX_ATTEMPTS attempts, the issues of a
   rejected review are given to the next attempt.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from agent_errors.errors import AgentRunError
from agent_runner.agent_runner import AgentRunner
from agents.outputs import AgentOutput
from agents.reviewer import ReviewOutput
from agents.triage import TriageOutput
from changeset.changeset import Changeset
from changeset.static_check import added_problems
from journal.run_journal import RunJournal
from policy.data_policy import DataPolicy
from startup.env_values import env_bool, env_int
from text_tools.failed_tests import failed_test_sources
from text_tools.log_cleaning import clean_log, error_summary, keep_tail, mask_secrets, strip_ansi
from tools.tool import Tool
from workflow_analysis.infra_failure import infra_triage
from workflow_analysis.task_messages import diagnosis_task, file_contents, fix_task, pipeline_context, review_task
from workflow_analysis.toolbox import build_run_registry
from workflow_remediation.ports import CodePlatform
from workflow_remediation.round_result import RoundResult
from workflow_remediation.transitions import STATUS_INFRA_FLAKY, STATUS_NOT_FIXABLE, STEP_PUBLISH, next_step

MAX_FIX_ATTEMPTS = env_int("MAX_FIX_ATTEMPTS", 2, minimum=1)

AUTO_RETRY_FLAKY = env_bool("AUTO_RETRY_FLAKY", False)

# Log lines of each failed job given to every agent, after the error summary of the whole log
CONTEXT_LOG_LINES = env_int("CONTEXT_LOG_LINES", 150)
MAX_CONTEXT_JOBS = env_int("MAX_CONTEXT_JOBS", 5, minimum=1)

# Error lines of each failed job shown on the web page for every round
ERRORS_SHOWN_PER_JOB = 30

# Code of the failed test functions given to every agent (characters in all)
FAILED_TESTS_MAX_CHARS = env_int("FAILED_TESTS_MAX_CHARS", 12000)

# Files given in full to the fixer and the reviewer, so they do not spend LLM calls reading them
TASK_FILE_MAX_CHARS = env_int("TASK_FILE_MAX_CHARS", 30000)
TASK_MAX_FILES = env_int("TASK_MAX_FILES", 8, minimum=1)


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
        calls_before = self.llm_calls()
        try:
            return self.run_agents(project, pipeline, allow_research)
        finally:
            self.log_llm_calls(pipeline, calls_before)

    def llm_calls(self) -> dict[str, int]:
        """LLM calls of the run so far, per agent (empty if the runner does not count them)."""
        calls = getattr(getattr(self.runner, "usage", None), "calls", None)
        return dict(calls) if isinstance(calls, dict) else {}

    def log_llm_calls(self, pipeline: dict[str, Any], before: dict[str, int]) -> None:
        """Record the LLM calls of this round per agent, to see where the budget goes."""
        after = self.llm_calls()
        per_agent = {name: count - before.get(name, 0) for name, count in after.items() if count > before.get(name, 0)}
        details = {"pipeline": pipeline.get("id"), "per_agent": per_agent, "round_calls": sum(per_agent.values())}
        details["run_calls"] = sum(after.values())
        self.journal.log("remediation_round", "llm_calls", details)

    def run_agents(self, project: str, pipeline: dict[str, Any], allow_research: bool) -> RoundResult:
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
        logs = []
        traces: dict[int, str] = {}
        for job in jobs[:MAX_CONTEXT_JOBS]:
            traces[int(job["id"])] = self.platform.job_trace(project, int(job["id"]))
            excerpt = clean_log(traces[int(job["id"])], CONTEXT_LOG_LINES)
            logs.append(f"Job {job.get('id')} ({job.get('name')}):\n{excerpt}")
        self.log_errors(pipeline, jobs, traces)
        context = pipeline_context(project, pipeline, jobs, "\n\n".join(logs))
        # The assertion alone does not show the inputs: give the code of the failed tests
        error_lines = [line for trace in traces.values() for line in error_summary(strip_ansi(trace), 200)]
        tests = failed_test_sources(changeset.current, error_lines, FAILED_TESTS_MAX_CHARS)
        if tests:
            context += f"\n\n## Code of the failed tests (as in the analysed commit)\n{tests}"
        if not self.analyse(project, context, result, jobs):
            return result
        self.fix_and_review(context, changeset, result)
        return result

    def log_errors(self, pipeline: dict[str, Any], jobs: list[dict[str, Any]], traces: dict[int, str]) -> None:
        """Record the error lines of each failed job, so the web page can show them for this round."""
        shown = []
        for job in jobs[:MAX_CONTEXT_JOBS]:
            cleaned = mask_secrets(strip_ansi(traces.get(int(job["id"]), "")))
            lines = error_summary(cleaned, ERRORS_SHOWN_PER_JOB)
            # No known error line: the end of the log usually says what failed
            if not lines:
                lines = [line for line in keep_tail(cleaned, ERRORS_SHOWN_PER_JOB // 2).splitlines() if line.strip()]
            shown.append({"job": str(job.get("name", "")), "url": job.get("web_url"), "lines": lines})
        details = {"pipeline": pipeline.get("id"), "pipeline_url": pipeline.get("web_url"), "jobs": shown}
        self.journal.log("remediation_round", "errors", details)

    def record_errors(self, project: str, pipeline: dict[str, Any]) -> None:
        """Record the errors of a pipeline without running the agents (round fixed by an earlier analysis)."""
        jobs = self.failed_jobs(project, pipeline)
        traces = {int(job["id"]): self.platform.job_trace(project, int(job["id"])) for job in jobs[:MAX_CONTEXT_JOBS]}
        self.log_errors(pipeline, jobs, traces)

    def analyse(
        self, project: str, context: str, result: RoundResult, jobs: list[dict[str, Any]] | None = None
    ) -> bool:
        """Run the triage, then the diagnosis if code can fix the failure; True if a diagnosis is ready.

        When GitLab already says that every failed job failed for an infrastructure reason
        (runner, timeout...), the triage result is known without an LLM call.
        """
        known = infra_triage(jobs or [])
        if known is not None:
            self.journal.log("triage", "start", {"source": "gitlab_failure_reason"})
            self.journal.log("triage", "finish", {"source": "gitlab_failure_reason", "output": known.model_dump()})
            result.triage = known
        else:
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
            paths = [*getattr(diagnosis, "files", []), *changeset.changed_files()]
            files = file_contents(changeset.current, paths, TASK_FILE_MAX_CHARS, TASK_MAX_FILES)
            result.fix = self.run_agent("fixer", fix_task(context, diagnosis, issues, files), result)
            if result.fix is None:
                return
            if changeset.is_empty():
                result.status = "no_change_proposed"
                return
            result.diff = changeset.diff()
            # Errors a static check finds (e.g. a name used after its import was removed) go back
            # to the fixer at once: the reviewer is not asked to approve a change that cannot run.
            problems = added_problems(changeset.files)
            if problems:
                self.journal.log("remediation_round", "static_check_failed", {"attempt": attempt, "problems": problems})
                issues = [f"Static check (pyflakes) of the proposed change: {problem}" for problem in problems]
                result.error = "; ".join(problems)
                continue
            changed = file_contents(changeset.current, changeset.changed_files(), TASK_FILE_MAX_CHARS, TASK_MAX_FILES)
            task = review_task(context, diagnosis, result.fix, result.diff, changed)
            result.review = self.run_agent("reviewer", task, result)
            if result.review is None:
                return
            if next_step(result.review) == STEP_PUBLISH:
                result.status = "fix_ready"
                result.error = ""
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
            # For the developers: the page only says that the analysis could not finish.
            self.journal.log(agent_name, "agent_failed", {"error": str(error)})
            return None

    def handle_flaky(self, project: str, triage: AgentOutput) -> str:
        """Retry the failed job if allowed (never in dry-run mode)."""
        if not self.auto_retry_flaky or self.dry_run or not isinstance(triage, TriageOutput):
            return STATUS_INFRA_FLAKY
        self.platform.retry_job(project, triage.failed_job_id)
        self.journal.log("remediation_round", "job_retried", {"job": triage.failed_job_id})
        return "infra_flaky_job_retried"
