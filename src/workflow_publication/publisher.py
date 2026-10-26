"""Publisher: creates the fix branch, commits the approved fix and opens a draft merge request.

These writes are done by code, never by an LLM: the agents only propose changes.
The merge request is always a draft, so a human reviews it.
"""

from __future__ import annotations

from typing import Any

from journal.run_journal import RunJournal
from workflow_publication.publication_texts import commit_actions, commit_message, merge_request_description
from workflow_remediation.ports import CodePlatform
from workflow_remediation.round_result import RoundResult


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
