"""Remediation round: runs the agents once on a failed pipeline, until a fix is approved or refused.

Order of one round (rules in workflow_remediation.transitions):
1. triage: stop for a transient infrastructure failure or a failure code cannot fix;
2. diagnosis: root cause and fix plan;
3. fixer <-> reviewer: up to MAX_FIX_ATTEMPTS attempts, the issues of a
   rejected review are given to the next attempt.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from agent_errors.errors import AgentRunError
from agent_runner.agent_runner import AgentRunner
from agents.diagnostic_agent import DiagnosisOutput
from agents.fixer_agent import FixOutput
from agents.outputs import AgentOutput
from agents.reviewer import ReviewOutput
from agents.triage import TriageOutput
from changeset.autofix import AUTOFIX_RUFF, AutofixResult, lint_report, ruff_autofix
from changeset.changeset import Changeset
from changeset.static_check import added_problems_with_lines
from journal.run_journal import RunJournal
from policy.data_policy import DataPolicy
from startup.env_values import env_bool, env_int
from text_tools.failed_tests import failed_test_sources
from text_tools.log_cleaning import clean_log, error_summary, keep_tail, mask_secrets, strip_ansi
from tools.tool import Tool
from workflow_analysis.fix_reuse import REUSE_FIXES, FixCache, failure_key, rebuild
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

# Error lines of each failed job given to the visitor API for every round
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
        # Key of the failure of the current round, for the cache of approved fixes
        self.reuse_key = ""

    def failed_jobs(self, project: str, pipeline: dict[str, Any]) -> list[dict[str, Any]]:
        """Return the failed jobs that block the pipeline, or every failed job if none blocks it."""
        jobs = self.platform.pipeline_jobs(project, int(pipeline["id"]), scope="failed")
        return [job for job in jobs if not job.get("allow_failure")] or jobs

    def run(self, project: str, pipeline: dict[str, Any], allow_research: bool, history: str = "") -> RoundResult:
        """`history`: the earlier fix attempts on this branch, added to the context of every agent."""
        calls_before = self.llm_calls()
        self.reuse_key = ""
        try:
            result = self.run_agents(project, pipeline, allow_research, history)
        finally:
            self.log_llm_calls(pipeline, calls_before)
        cache = self.fix_cache()
        # An approved fix is kept so that the same failure on the same commit needs no LLM call next time
        if cache is not None and self.reuse_key and result.status == "fix_ready":
            cache.save(self.reuse_key, result, str(getattr(self.journal, "run_id", "")))
        result.reuse_key = self.reuse_key
        return result

    def forget_fix(self, result: RoundResult) -> None:
        """A fix whose pipeline failed must not be reused: remove it from the cache."""
        cache = self.fix_cache()
        key = str(getattr(result, "reuse_key", None) or "")
        if cache is not None and key:
            cache.forget(key)

    def fix_cache(self) -> FixCache | None:
        """The cache of approved fixes, in the runs folder (None when disabled or without a real journal)."""
        folder = getattr(self.journal, "folder", None)
        if not REUSE_FIXES or not isinstance(folder, Path):
            return None
        return FixCache(folder.parent)

    def reuse_fix(self, changeset: Changeset, result: RoundResult) -> bool:
        """Fill the round with the fix saved for the same failure on the same commit; True if one was used."""
        cache = self.fix_cache()
        data = cache.load(self.reuse_key) if cache is not None and self.reuse_key else None
        if data is None or not rebuild(data, result):
            return False
        files = data["files"]
        valid = isinstance(files, dict) and all(
            isinstance(k, str) and isinstance(v, str) and not k.startswith("/") and ".." not in k.split("/")
            for k, v in files.items()
        )
        if not valid:
            return False
        for path, content in files.items():
            changeset.replace_file(path, content)
        result.diff = changeset.diff()
        result.files = changeset.changed_files()
        result.status = "fix_ready"
        self.journal.log("remediation_round", "fix_reused", {"from_run": data.get("run_id"), "files": sorted(files)})
        self.mark_agents_done("reused fix")
        return True

    def mark_agents_done(self, source: str) -> None:
        """Mark the four agents as done in the journal when the round needed none of them."""
        for agent in ("triage", "diagnosis", "fixer", "reviewer"):
            self.journal.log(agent, "start", {"source": source})
            self.journal.log(agent, "finish", {"source": source})

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
        totals = getattr(getattr(self.runner, "usage", None), "totals", None)
        if callable(totals):
            tokens = totals()
            if isinstance(tokens, dict):
                # Share of the input the provider read from its cache: the higher, the cheaper the run
                details["run_tokens"] = tokens
        self.journal.log("remediation_round", "llm_calls", details)

    def run_agents(
        self, project: str, pipeline: dict[str, Any], allow_research: bool, history: str = ""
    ) -> RoundResult:
        result = RoundResult(pipeline_id=int(pipeline["id"]))
        jobs = self.failed_jobs(project, pipeline)
        if not jobs:
            result.status = "no_failed_jobs"
            return result
        sha = str(pipeline.get("sha", ""))
        # Files are read from the analysed commit, not from the branch head
        # propose_edits refuses edits that add a Python error, so the fixer repairs them in the same conversation
        changeset = Changeset(lambda path: self.platform.file_raw(project, path, sha), check=added_problems_with_lines)
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
        # The same failure on the same commit: reuse the fix already approved, without any agent
        if self.fix_cache() is not None:
            self.reuse_key = failure_key(project, sha, jobs[:MAX_CONTEXT_JOBS], traces)
            if self.reuse_fix(changeset, result):
                return result
        context = pipeline_context(project, pipeline, jobs, "\n\n".join(logs))
        # Lint and format errors that ruff fixes on its own cost no LLM call
        autofix = self.autofix(changeset, jobs, traces)
        # A fix made by ruff alone still goes through the static check, like any fix
        if autofix.complete() and not added_problems_with_lines(changeset.files):
            self.deterministic_fix(jobs, autofix, changeset, result)
            return result
        if autofix.files:
            context += (
                "\n\n## Already fixed automatically by ruff (no need to redo it)\n"
                f"Files: {', '.join(sorted(autofix.files))}. The file contents you get include these fixes; "
                "fix only the other errors."
            )
        # The assertion alone does not show the inputs: give the code of the failed tests
        error_lines = [line for trace in traces.values() for line in error_summary(strip_ansi(trace), 200)]
        tests = failed_test_sources(changeset.current, error_lines, FAILED_TESTS_MAX_CHARS)
        if tests:
            context += f"\n\n## Code of the failed tests (as in the analysed commit)\n{tests}"
        if history:
            context += f"\n\n{history}"
        if not self.analyse(project, context, result, jobs):
            return result
        self.fix_and_review(context, changeset, result)
        return result

    def autofix(self, changeset: Changeset, jobs: list[dict[str, Any]], traces: dict[int, str]) -> AutofixResult:
        """Apply ruff's own fixes for the lint and format errors of the failed jobs."""
        if not AUTOFIX_RUFF:
            return AutofixResult()
        result = ruff_autofix(changeset.current, lint_report(traces.values()))
        for path, content in result.files.items():
            changeset.replace_file(path, content)
        if result.files:
            details = {"files": sorted(result.files), "remaining": result.remaining, "complete": result.complete()}
            self.journal.log("remediation_round", "ruff_autofix", details)
        return result

    def deterministic_fix(
        self, jobs: list[dict[str, Any]], autofix: AutofixResult, changeset: Changeset, result: RoundResult
    ) -> None:
        """Fill the round as the agents would, for a fix made by ruff alone (no LLM call, no review needed)."""
        files = sorted(autofix.files)
        rules = ", ".join(sorted(autofix.report.codes)) or "formatting"
        summary = f"Lint and format errors fixed by ruff ({rules})."
        result.triage = TriageOutput(
            summary=summary,
            category="code_bug",
            failed_job_id=int(jobs[0]["id"]),
            log_excerpt=f"ruff: {rules} in {', '.join(files)}"[:2000],
            fixable_by_code=True,
        )
        result.diagnosis = DiagnosisOutput(
            summary=summary,
            root_cause=f"The lint job reports errors that ruff can fix itself ({rules}).",
            files=files,
            evidence=f"ruff errors in the job log for {', '.join(files)}.",
            fix_plan="Apply ruff check --fix (safe fixes, reported rules only) and ruff format.",
            confidence="high",
        )
        result.fix = FixOutput(
            summary="Applied ruff's own fixes; no LLM was used.",
            files_changed=files,
            tests_modified=any(path.startswith(("tests/", "test/")) for path in files),
        )
        result.review = ReviewOutput(
            summary="Deterministic fix by ruff.",
            approved=True,
            risk="low",
            comment="Made by ruff (safe fixes only, rules reported by the pipeline); the next pipeline confirms it.",
        )
        result.diff = changeset.diff()
        result.files = changeset.changed_files()
        result.status = "fix_ready"
        self.mark_agents_done("ruff")

    def log_errors(self, pipeline: dict[str, Any], jobs: list[dict[str, Any]], traces: dict[int, str]) -> None:
        """Record the error lines of each failed job, so the visitor API can give them for this round."""
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
        static_check_failed = False
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
            problems = added_problems_with_lines(changeset.files)
            if problems:
                self.journal.log("remediation_round", "static_check_failed", {"attempt": attempt, "problems": problems})
                issues = [f"Static check (pyflakes) of the proposed change: {problem}" for problem in problems]
                result.error = "; ".join(problem.splitlines()[0] for problem in problems)
                static_check_failed = True
                continue
            static_check_failed = False
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
        # The last attempt may never have reached the reviewer
        result.status = "static_check_failed" if static_check_failed else "review_rejected"

    def run_agent(self, agent_name: str, task: str, result: RoundResult) -> AgentOutput | None:
        """Return the agent's validated output, or None after recording its failure in the round."""
        try:
            return self.runner.run(agent_name, task)
        except AgentRunError as error:
            result.status = f"{agent_name}_failed"
            result.error = str(error)
            # For the developers: visitors only learn that the analysis could not finish.
            self.journal.log(agent_name, "agent_failed", {"error": str(error)})
            return None

    def handle_flaky(self, project: str, triage: AgentOutput) -> str:
        """Retry the failed job if allowed (never in dry-run mode)."""
        if not self.auto_retry_flaky or self.dry_run or not isinstance(triage, TriageOutput):
            return STATUS_INFRA_FLAKY
        self.platform.retry_job(project, triage.failed_job_id)
        self.journal.log("remediation_round", "job_retried", {"job": triage.failed_job_id})
        return "infra_flaky_job_retried"
