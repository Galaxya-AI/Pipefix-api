"""Publisher: creates the fix branch, commits the approved fix and opens a draft merge request.

These writes are done by code, never by an LLM: the agents only propose changes.
The merge request is always a draft, so a human reviews it.
"""

from __future__ import annotations

import json
from typing import Any

from agents.diagnostic_agent import DiagnosisOutput
from agents.fixer_agent import FixOutput
from agents.reviewer import ReviewOutput
from agents.triage import TriageOutput
from journal.run_journal import RunJournal
from workflow_publication.publication_texts import (
    commit_actions,
    commit_message,
    fix_branch_name,
    merge_request_description,
)
from workflow_remediation.ports import CodePlatform
from workflow_remediation.round_result import RoundResult

# Written by a dry run: the analysed pipeline and the new content of the changed files.
SAVED_FIX_FILE = "fix_round.json"

# Written once the saved fix is published, so it is never published twice.
MERGE_REQUEST_FILE = "merge_request.url"


class Publisher:
    def __init__(self, platform: CodePlatform, journal: RunJournal) -> None:
        self.platform = platform
        self.journal = journal

    def create_branch(self, project: str, branch: str, ref: str) -> dict[str, Any]:
        """Create the fix branch from `ref`, the commit analysed by the agents."""
        created = self.platform.create_branch(project, branch, ref)
        self.journal.log("publisher", "branch", {"branch": branch, "from": ref})
        return created

    def commit(self, project: str, branch: str, result: RoundResult) -> dict[str, Any]:
        actions = commit_actions(result)
        commit = self.platform.create_commit(project, branch, commit_message(result), actions)
        self.journal.log("publisher", "commit", {"branch": branch, "sha": commit.get("id"), "files": len(actions)})
        return commit

    def open_merge_request(
        self,
        project: str,
        branch: str,
        target_branch: str,
        pipeline: dict[str, Any],
        result: RoundResult,
        base_moved: bool = False,
    ) -> dict[str, Any]:
        """Open the draft merge request; `base_moved` means the target got commits after the analysed one.

        An open merge request of the branch is reused: the answer of an earlier creation may have been lost.
        """
        existing = self.platform.find_merge_request(project, branch)
        if existing is not None:
            self.journal.log("publisher", "merge_request_reused", {"url": existing.get("web_url")})
            return existing
        title = f"Draft: Fix for pipeline #{pipeline.get('id')} on {target_branch}"
        merge_request = self.platform.create_merge_request(
            project, branch, target_branch, title, merge_request_description(pipeline, result, base_moved)
        )
        self.journal.log("publisher", "merge_request", {"url": merge_request.get("web_url")})
        return merge_request


def publish_saved_fix(platform: CodePlatform, journal: RunJournal, report: dict[str, Any]) -> str:
    """Publish the fix of a finished dry run exactly as it was shown, and return the merge request URL."""
    published = journal.folder / MERGE_REQUEST_FILE
    if published.is_file():
        return published.read_text(encoding="utf-8")
    saved = json.loads((journal.folder / SAVED_FIX_FILE).read_text(encoding="utf-8"))
    pipeline: dict[str, Any] = saved["pipeline"]
    last = report["rounds"][-1]
    result = RoundResult(
        pipeline_id=int(pipeline["id"]),
        status="fix_ready",
        triage=TriageOutput.model_validate(last["triage"]),
        diagnosis=DiagnosisOutput.model_validate(last["diagnosis"]),
        fix=FixOutput.model_validate(last["fix"]),
        review=ReviewOutput.model_validate(last["review"]),
        files=saved["files"],
    )
    project = str(report["project"])
    target_branch = str(pipeline.get("ref", ""))
    analysed_sha = str(pipeline.get("sha", ""))
    branch = str(saved.get("branch") or fix_branch_name(result.pipeline_id))
    publisher = Publisher(platform, journal)
    if not platform.branch_exists(project, branch):
        publisher.create_branch(project, branch, analysed_sha)
    publisher.commit(project, branch, result)
    base_moved = platform.branch_head(project, target_branch) != analysed_sha
    merge_request = publisher.open_merge_request(project, branch, target_branch, pipeline, result, base_moved)
    url = str(merge_request.get("web_url", ""))
    journal.write_text(MERGE_REQUEST_FILE, url)
    return url
