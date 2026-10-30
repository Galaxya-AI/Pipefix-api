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
        # An open MR is not proof that the pipeline was repaired. Reanalyse, but
        # publish on an isolated branch so the pending MR is never overwritten.
        original_branch = fix_branch_name(int(pipeline["id"]))
        existing = self.platform.find_merge_request(project, original_branch)
        branch = retry_branch_name(int(pipeline["id"]), self.journal.run_id) if existing else original_branch
        report["fix_branch"] = branch
        if existing:
            report["previous_merge_request_url"] = existing.get("web_url")
            report["warning"] = (
                "A previous fix is still open; this analysis is independent. "
                "Review both proposals and avoid merging conflicting changes."
            )
            self.journal.log("workflow", "previous_fix_open", {"url": existing.get("web_url"), "new_branch": branch})
        return self.run_rounds(project, pipeline, decision.allow_research, report)

    def run_rounds(self, project: str, pipeline: dict[str, Any], allow_research: bool, report: dict[str, Any]) -> str:
        """Run "analyse -> publish -> wait" rounds until a final status."""
        current = pipeline
        seen_failures: set[tuple[str, ...]] = set()
        for round_number in range(1, self.max_rounds + 1):
            self.journal.log("workflow", "round", {"number": round_number, "pipeline": current.get("id")})
            # Track remaining failures without treating the same job names as proof
            # of a successful repair. Repeated failures are surfaced for human review.
            failing_jobs = self.platform.pipeline_jobs(project, int(current["id"]), scope="failed") 
            if self.wait_for_pipeline and not self.dry_run 
            else []
            signature = failed_job_signature(failing_jobs)
            if signature and signature in seen_failures:
                report["warning"] = "The same jobs failed after an attempted fix; manual review is required."
                return "repeated_failure"
            if signature:
                seen_failures.add(signature)
            result = self.remediation_round.run(project, current, allow_research)
            report["rounds"].append(result.as_report())
            if result.status != "fix_ready":
                return result.status
            if self.dry_run:
                report["validation"] = {"verified": False, "reason": "dry_run"}
                report["patch_file"] = str(self.journal.write_text("fix.patch", result.diff))
                # Lets the web page publish this exact fix later, without a new analysis.
                self.journal.write_text(SAVED_FIX_FILE, json.dumps(
                    {"pipeline": pipeline, 
                    "files": result.files, 
                    "branch": report["fix_branch"]}
                    )
                )
                return "dry_run_patch_ready"
            commit = self.publish_fix(project, pipeline, result, report)
            if not self.wait_for_pipeline:
                report["validation"] = {"verified": False, "reason": "not_requested"}
                return "merge_request_opened"
            finished: dict[str, Any] | None = self.waiter.wait(
                project, str(report["fix_branch"]), str(commit.get("id", "")), self.pipeline_timeout_s
            )
            record = validation_record(finished, str(commit.get("id", "")))
            report.setdefault("validations", []).append(record)
            report["validation"] = record
            if record["reason"] == "sha_mismatch":
                return "pipeline_sha_mismatch"
            status: str | None = outcome_status(finished)
            if status is not None or finished is None:
                return status or "pipeline_timeout"
            # Still failing: new round on the fix pipeline
            current = finished
        report["validation"] = {"verified": False, "reason": "max_rounds_reached"}
        return "max_rounds_reached"

    def publish_fix(
        self, project: str, pipeline: dict[str, Any], result: RoundResult, report: dict[str, Any]
    ) -> dict[str, Any]:
        """Publish an approved fix: fix branch if missing, commit, merge request on the first round.

        The fix branch starts from the analysed commit, not from the current head
        of the failing branch: the fix was built from the files of that commit, so
        newer commits are never overwritten; GitLab shows a conflict instead.
        """
        target_branch = str(pipeline.get("ref", ""))
        analysed_sha = str(pipeline.get("sha", ""))
        branch = str(report.get("fix_branch") or fix_branch_name(int(pipeline["id"])))
        if not self.platform.branch_exists(project, branch):
            self.publisher.create_branch(project, branch, analysed_sha)
        commit = self.publisher.commit(project, branch, result)
        if "merge_request" not in report:
            base_moved = self.platform.branch_head(project, target_branch) != analysed_sha
            report["base_moved"] = base_moved
            merge_request = self.publisher.open_merge_request(
                project, branch, target_branch, pipeline, result, base_moved
            )
            report["merge_request"] = merge_request.get("web_url")
        return commit
