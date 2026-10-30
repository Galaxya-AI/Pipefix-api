"""Remediation workflow: runs the agents in a fixed order on a failed pipeline and publishes the approved fix.

The order is fixed in code (see workflow_remediation.transitions); no LLM decides which agent runs next.
One run has two phases:
- analysis (workflow_analysis/): data policy, choice of the pipeline, one
  round of agents (triage -> diagnosis -> fixer <-> reviewer);
- publication (workflow_publication/): fix branch, commit and draft merge
  request, then optionally waiting for the pipeline of the fix, which starts
  a new round if it fails again.
In dry-run mode, the patch is only written to the run folder.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from typing import Any

from agent_base.agent_base import AgentConfig
from agent_runner.agent_runner import AgentRunner
from agent_runner.runner_limits import RunnerLimits
from connectors.errors.errors import ConnectorError
from journal.run_journal import RunJournal
from llm.base import LLMClient
from policy.data_policy import DataPolicy
from tools.registry import ToolRegistry
from tools.tool import Tool
from workflow_analysis.pipeline_selection import find_pipeline, refusal_status
from workflow_analysis.policy_check import STATUS_BLOCKED, check_project_policy
from workflow_analysis.remediation_round import RemediationRound
from workflow_publication.failure_notifier import FailureNotifier
from workflow_publication.pipeline_waiter import PipelineWaiter, outcome_status
from workflow_publication.publication_texts import fix_branch_name, retry_branch_name
from workflow_publication.publisher import SAVED_FIX_FILE, Publisher
from workflow_publication.validation import failed_job_signature, validation_record
from workflow_remediation.ports import CodePlatform
from workflow_remediation.round_result import RoundResult

# Dry-run mode: write the patch, publish nothing
DRY_RUN = os.getenv("DRY_RUN", "true").strip().lower() in ("1", "true", "yes")

# Rounds "fix -> wait for the pipeline -> fix again"
MAX_ROUNDS = int(os.getenv("MAX_ROUNDS", "1"))

WAIT_FOR_PIPELINE = os.getenv("WAIT_FOR_PIPELINE", "false").strip().lower() in ("1", "true", "yes")

PIPELINE_TIMEOUT_S = int(os.getenv("PIPELINE_TIMEOUT_S", "1800"))


class RemediationWorkflow:
    """Run the remediation of a failed pipeline: analysis, then publication.

    Collaborators left to None are built on the platform with the values from .env.
    """

    def __init__(
        self,
        platform: CodePlatform,
        llm: LLMClient,
        agents: dict[str, AgentConfig],
        tools: Iterable[Tool],
        journal: RunJournal,
        policy: DataPolicy | None = None,
        limits: RunnerLimits | None = None,
        dry_run: bool = DRY_RUN,
        max_rounds: int = MAX_ROUNDS,
        wait_for_pipeline: bool = WAIT_FOR_PIPELINE,
        pipeline_timeout_s: int = PIPELINE_TIMEOUT_S,
        remediation_round: RemediationRound | None = None,
        publisher: Publisher | None = None,
        waiter: PipelineWaiter | None = None,
        notifier: FailureNotifier | None = None,
    ) -> None:
        self.platform = platform
        self.agents = agents
        self.journal = journal
        self.policy = policy or DataPolicy.from_environment()
        self.dry_run = dry_run
        self.max_rounds = max(1, max_rounds)
        self.wait_for_pipeline = wait_for_pipeline
        self.pipeline_timeout_s = pipeline_timeout_s
        # Shared by every round so the LLM usage adds up
        self.runner = AgentRunner(llm, ToolRegistry(), agents, limits, journal)
        self.remediation_round = remediation_round or RemediationRound(
            platform, self.runner, tools, self.policy, journal, dry_run
        )
        self.publisher = publisher or Publisher(platform, journal)
        self.waiter = waiter or PipelineWaiter(platform, journal)
        self.notifier = notifier or FailureNotifier(platform, journal)

    def run(self, project: str, pipeline_id: int | None = None) -> dict[str, Any]:
        """Remediate a failed pipeline (the latest one when no id is given); the report also goes to report.json."""
        report: dict[str, Any] = {
            "run_id": self.journal.run_id,
            "project": project,
            "dry_run": self.dry_run,
            "policy": self.policy.as_dict(),
            "rounds": [],
        }
        self.journal.log("workflow", "start", {"project": project, "pipeline_id": pipeline_id})
        try:
            report["status"] = self.process(project, pipeline_id, report)
        # Network, token, unknown project...: reported, not raised
        except ConnectorError as error:
            report["status"] = "error"
            report["error"] = str(error)
        report["notification"] = self.notifier.notify(project, report, self.dry_run)
        report["usage"] = {"llm_calls": self.runner.usage.total_calls(), "per_agent": self.runner.usage.as_dict()}
        self.journal.write_report(report)
        self.journal.log("workflow", "done", {"status": report.get("status")})
        return report

    def process(self, project: str, pipeline_id: int | None, report: dict[str, Any]) -> str:
        """Check the policy, choose the pipeline, then run the rounds; return the final status."""
        # Data policy first, before reading any log or file
        decision = check_project_policy(self.platform, self.policy, self.agents.values(), project, self.journal)
        if not decision.allowed:
            report["error"] = decision.reason
            return STATUS_BLOCKED
        pipeline: dict[str, Any] | None = find_pipeline(self.platform, project, pipeline_id)
        if pipeline is None:
            return "no_failed_pipeline"
        report["pipeline"] = {"id": pipeline.get("id"), "branch": pipeline.get("ref"), "url": pipeline.get("web_url")}
        refusal: str | None = refusal_status(pipeline)
        if refusal is not None:
            return refusal
        original_branch = fix_branch_name(int(pipeline["id"]))
        existing = self.platform.find_merge_request(project, original_branch)
        branch = (
            retry_branch_name(int(pipeline["id"]), self.journal.run_id)
            if existing
            else fix_branch_name(int(pipeline["id"]), self.journal.run_id)
        )
        report["fix_branch"] = branch
        if existing:
            report["previous_merge_request_url"] = existing.get("web_url")
            report["warning"] = (
                "A previous fix is still open; this analysis is independent. "
                "Review both proposals and avoid merging conflicting changes."
            )
            self.journal.log(
                "workflow",
                "previous_fix_open",
                {"url": existing.get("web_url"), "new_branch": branch},
            )
        return self.run_rounds(project, pipeline, decision.allow_research, report)

    def run_rounds(self, project: str, pipeline: dict[str, Any], allow_research: bool, report: dict[str, Any]) -> str:
        """Accumulate corrections on one branch; open a draft MR only after CI succeeds.

        With WAIT_FOR_PIPELINE disabled we retain the original single-fix workflow.
        Every wait is pinned to the SHA returned by GitLab, not just the branch.
        """
        current = pipeline
        branch = fix_branch_name(int(pipeline["id"]), self.journal.run_id)
        seen_failures: set[tuple[str, ...]] = set()
        last_result: RoundResult | None = None
        for round_number in range(1, self.max_rounds + 1):
            self.journal.log("workflow", "round", {"number": round_number, "pipeline": current.get("id")})
            if self.wait_for_pipeline and not self.dry_run:
                failing_jobs = self.platform.pipeline_jobs(project, int(current["id"]), scope="failed")
                signature = tuple(
                    sorted(
                        f"{job.get('name', '')}:{job.get('failure_reason', '')}:"
                        f"{self.platform.job_trace(project, int(job['id']))[-1000:]}"
                        for job in failing_jobs
                    )
                )
                if signature and signature in seen_failures:
                    report["error"] = "The same failed jobs were observed again; manual review required."
                    return "repeated_failure"
                if signature:
                    seen_failures.add(signature)
            result = self.remediation_round.run(project, current, allow_research)
            report["rounds"].append(result.as_report())
            if result.status != "fix_ready":
                return result.status
            if self.dry_run:
                report["patch_file"] = str(self.journal.write_text("fix.patch", result.diff))
                self.journal.write_text(SAVED_FIX_FILE, json.dumps({"pipeline": pipeline, "files": result.files}))
                return "dry_run_patch_ready"

            # Fixes in later rounds are applied to the SAME working branch.
            # Do not publish an MR until the full fix-branch pipeline has passed.
            last_result = result
            commit = self.commit_fix(project, pipeline, branch, result)
            sha = str(commit.get("id", ""))
            report["fix_branch"] = branch
            report.setdefault("validation", []).append({"round": round_number, "commit_sha": sha})
            if not self.wait_for_pipeline:
                self.open_fix_mr(project, pipeline, branch, result, report)
                return "merge_request_opened"
            if not sha:
                report["error"] = "GitLab did not return a commit SHA; cannot verify the pipeline."
                return "validation_unavailable"
            finished = self.waiter.wait(project, branch, sha, self.pipeline_timeout_s)
            validation = report["validation"][-1]
            validation["pipeline_id"] = finished.get("id") if finished else None
            validation["status"] = finished.get("status") if finished else "timeout"
            if finished is not None and str(finished.get("sha", "")) != sha:
                report["error"] = "Pipeline SHA does not match the proposed fix commit."
                return "validation_sha_mismatch"
            status = outcome_status(finished)
            if status == "pipeline_fixed":
                self.open_fix_mr(project, pipeline, branch, last_result, report)
                return "pipeline_fixed"
            if status is not None:
                return status
            # The next round reads files at this fix-branch pipeline's commit.
            current = finished
        report["error"] = "The iteration limit was reached; no MR was opened without passing CI."
        return "max_rounds_reached"

    def commit_fix(
        self, project: str, pipeline: dict[str, Any], branch: str, result: RoundResult
    ) -> dict[str, Any]:
        """Commit on the isolated work branch, never directly on the target."""
        if not self.platform.branch_exists(project, branch):
            self.publisher.create_branch(project, branch, str(pipeline.get("sha", "")))
        return self.publisher.commit(project, branch, result)

    def open_fix_mr(
        self,
        project: str,
        pipeline: dict[str, Any],
        branch: str,
        result: RoundResult,
        report: dict[str, Any],
    ) -> None:
        target = str(pipeline.get("ref", ""))
        base_moved = self.platform.branch_head(project, target) != str(pipeline.get("sha", ""))
        report["base_moved"] = base_moved
        if base_moved:
            report["error"] = (
                "Target branch advanced since diagnosis; re-run analysis on the new base "
                "before opening a merge request."
            )
            raise ConnectorError(report["error"])
        mr = self.publisher.open_merge_request(project, branch, target, pipeline, result, base_moved)
        report["merge_request"] = mr.get("web_url")
