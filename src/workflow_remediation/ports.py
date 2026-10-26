"""Code platform interface used by the workflow.

GitLabClient implements it; another platform can be plugged in with a class
exposing the same methods, without changing the workflow.
"""

from __future__ import annotations

from typing import Any, Protocol


class CodePlatform(Protocol):
    def get_project(self, project: str | int) -> dict[str, Any]: ...

    def get_pipeline(self, project: str | int, pipeline_id: int) -> dict[str, Any]: ...

    def list_pipelines(self, project: str | int, **filters: Any) -> list[dict[str, Any]]:
        """Return pipelines, newest first."""
        ...

    def pipeline_jobs(self, project: str | int, pipeline_id: int, scope: str | None = None) -> list[dict[str, Any]]: ...

    def job_trace(self, project: str | int, job_id: int) -> str:
        """Return the raw log of a job."""
        ...

    def retry_job(self, project: str | int, job_id: int) -> dict[str, Any]: ...

    def file_raw(self, project: str | int, path: str, ref: str) -> str: ...

    def branch_exists(self, project: str | int, branch: str) -> bool: ...

    def is_protected_branch(self, project: str | int, branch: str) -> bool:
        """True for the default branch or a protected branch of the project."""
        ...

    def create_branch(self, project: str | int, branch: str, ref: str) -> dict[str, Any]: ...

    def create_commit(
        self, project: str | int, branch: str, message: str, actions: list[dict[str, str]]
    ) -> dict[str, Any]:
        """Create a commit on an existing branch."""
        ...

    def find_merge_request(self, project: str | int, source_branch: str) -> dict[str, Any] | None:
        """Return the open merge request of a branch, or None."""
        ...

    def create_merge_request(
        self, project: str | int, source_branch: str, target_branch: str, title: str, description: str
    ) -> dict[str, Any]: ...

    def branch_head(self, project: str | int, branch: str) -> str:
        """Return the id of the latest commit of a branch."""
        ...

    def list_open_issues(self, project: str | int, label: str) -> list[dict[str, Any]]: ...

    def create_issue(self, project: str | int, title: str, description: str, labels: list[str]) -> dict[str, Any]: ...

    def add_issue_note(self, project: str | int, issue_iid: int, body: str) -> dict[str, Any]: ...
