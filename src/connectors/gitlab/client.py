"""GitLab REST API (v4) client.

Only HTTP calls, no agent logic. It is used:
- by the GitLab connector, for the read-only tools given to the agents;
- by the workflow, to read pipelines and publish fixes (branch, commit
  and merge request), steps that are kept out of the LLM's reach.
Works with gitlab.com and with a self-hosted GitLab instance.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import requests

from connectors.errors.errors import ConnectorError
from connectors.http_client.http_api import HttpApi


def encode(value: str | int) -> str:
    """Encode a project, a file path or a branch for a URL ("/" included).

    Parameters
    ----------
    value : str or int
        Value to encode.

    Returns
    -------
    str
        Encoded value.
    """
    # "/" is encoded too, as GitLab expects
    return quote(str(value), safe="")


class GitLabClient(HttpApi):
    """GitLab API calls used by the system.

    Parameters
    ----------
    base_url : str
        Address of the GitLab instance, e.g. https://gitlab.com.
    token : str
        Personal or project access token.
    timeout_s : int, default 30
        Maximum duration of a request, in seconds.
    """

    def __init__(self, base_url: str, token: str, timeout_s: int = 30) -> None:
        # HTTP client on the API v4, with the token header
        super().__init__(f"{base_url.rstrip('/')}/api/v4", {"PRIVATE-TOKEN": token}, timeout_s)

    def get_project(self, project: str | int) -> dict[str, Any]:
        """Return the project information (id, path, default branch...).

        Parameters
        ----------
        project : str or int
            Project id or "group/project" path.

        Returns
        -------
        dict[str, Any]
            The project.
        """
        # Read the project
        project_data: dict[str, Any] = self.get_json(f"/projects/{encode(project)}")
        # Project
        return project_data

    def get_pipeline(self, project: str | int, pipeline_id: int) -> dict[str, Any]:
        """Return one pipeline (id, status, ref, sha...).

        Parameters
        ----------
        project : str or int
            Project.
        pipeline_id : int
            Pipeline id.

        Returns
        -------
        dict[str, Any]
            The pipeline.
        """
        # Read the pipeline
        pipeline: dict[str, Any] = self.get_json(f"/projects/{encode(project)}/pipelines/{pipeline_id}")
        # Pipeline
        return pipeline

    def list_pipelines(self, project: str | int, **filters: Any) -> list[dict[str, Any]]:
        """Return pipelines, newest first.

        Parameters
        ----------
        project : str or int
            Project.
        **filters : Any
            GitLab filters (status, ref, sha, updated_after, per_page...).

        Returns
        -------
        list[dict[str, Any]]
            The pipelines.
        """
        # Newest first, then the given filters
        params: dict[str, Any] = {"order_by": "id", "sort": "desc", **filters}
        # Read the pipelines
        return list(self.get_json(f"/projects/{encode(project)}/pipelines", params))

    def pipeline_jobs(self, project: str | int, pipeline_id: int, scope: str | None = None) -> list[dict[str, Any]]:
        """Return the jobs of a pipeline.

        Parameters
        ----------
        project : str or int
            Project.
        pipeline_id : int
            Pipeline id.
        scope : str or None, default None
            Job status filter, e.g. "failed".

        Returns
        -------
        list[dict[str, Any]]
            The jobs.
        """
        # Up to 100 jobs
        params: dict[str, Any] = {"per_page": 100}
        # Status filter
        if scope:
            # Added to the parameters
            params["scope[]"] = scope
        # Read the jobs
        return list(self.get_json(f"/projects/{encode(project)}/pipelines/{pipeline_id}/jobs", params))

    def job_trace(self, project: str | int, job_id: int) -> str:
        """Return the raw log of a job.

        Parameters
        ----------
        project : str or int
            Project.
        job_id : int
            Job id.

        Returns
        -------
        str
            Full log.
        """
        # Read the log
        return self.get_text(f"/projects/{encode(project)}/jobs/{job_id}/trace")

    def retry_job(self, project: str | int, job_id: int) -> dict[str, Any]:
        """Retry a job.

        Parameters
        ----------
        project : str or int
            Project.
        job_id : int
            Job id.

        Returns
        -------
        dict[str, Any]
            The new job.
        """
        # Retry request
        job: dict[str, Any] = self.post_json(f"/projects/{encode(project)}/jobs/{job_id}/retry") or {}
        # New job
        return job

    def list_tree(self, project: str | int, ref: str, max_pages: int = 50) -> list[dict[str, Any]]:
        """Return the full repository tree, page by page.

        Parameters
        ----------
        project : str or int
            Project.
        ref : str
            Branch, tag or commit.
        max_pages : int, default 50
            Maximum number of pages of 100 entries.

        Returns
        -------
        list[dict[str, Any]]
            Entries (files and folders).
        """
        # Entries read so far
        items: list[dict[str, Any]] = []
        # Current page
        page: int = 1
        # While pages are allowed
        while page <= max_pages:
            # One page of the tree
            response: requests.Response = self.request(
                "GET",
                f"/projects/{encode(project)}/repository/tree",
                params={"ref": ref, "recursive": "true", "per_page": 100, "page": page},
            )
            # Add its entries
            items.extend(response.json())
            # Next page given by GitLab, empty on the last page
            next_page: str = response.headers.get("X-Next-Page", "")
            # No more pages
            if not next_page:
                # Stop
                break
            # Move to the next page
            page = int(next_page)
        # Full tree
        return items

    def file_raw(self, project: str | int, path: str, ref: str) -> str:
        """Return the raw content of a file.

        Parameters
        ----------
        project : str or int
            Project.
        path : str
            File path in the repository.
        ref : str
            Branch, tag or commit.

        Returns
        -------
        str
            File content.
        """
        # Read the file
        return self.get_text(f"/projects/{encode(project)}/repository/files/{encode(path)}/raw", {"ref": ref})

    def recent_commits(self, project: str | int, ref: str, limit: int = 10) -> list[dict[str, Any]]:
        """Return the latest commits of a reference.

        Parameters
        ----------
        project : str or int
            Project.
        ref : str
            Branch, tag or commit.
        limit : int, default 10
            Number of commits.

        Returns
        -------
        list[dict[str, Any]]
            The commits, newest first.
        """
        # Read the commits
        return list(
            self.get_json(f"/projects/{encode(project)}/repository/commits", {"ref_name": ref, "per_page": limit})
        )

    def commit_diff(self, project: str | int, sha: str) -> list[dict[str, Any]]:
        """Return the files changed by a commit.

        Parameters
        ----------
        project : str or int
            Project.
        sha : str
            Commit id.

        Returns
        -------
        list[dict[str, Any]]
            Diffs per file.
        """
        # Read the diff
        return list(self.get_json(f"/projects/{encode(project)}/repository/commits/{encode(sha)}/diff"))

    def branch_exists(self, project: str | int, branch: str) -> bool:
        """Tell whether a branch exists.

        Parameters
        ----------
        project : str or int
            Project.
        branch : str
            Branch name.

        Returns
        -------
        bool
            True if the branch exists.

        Raises
        ------
        ConnectorError
            On any error other than "not found".
        """
        # Read the branch
        try:
            # Branch found
            self.get_json(f"/projects/{encode(project)}/repository/branches/{encode(branch)}")
        # Error
        except ConnectorError as error:
            # Branch missing
            if error.status_code == 404:
                # Negative answer
                return False
            # Any other error goes up
            raise
        # Branch exists
        return True

    def branch_head(self, project: str | int, branch: str) -> str:
        """Return the id of the latest commit of a branch.

        Parameters
        ----------
        project : str or int
            Project.
        branch : str
            Branch name.

        Returns
        -------
        str
            Commit id (empty if GitLab gives none).
        """
        # Read the branch
        data: dict[str, Any] = self.get_json(f"/projects/{encode(project)}/repository/branches/{encode(branch)}")
        # Id of its latest commit
        return str((data.get("commit") or {}).get("id", ""))

    def create_branch(self, project: str | int, branch: str, ref: str) -> dict[str, Any]:
        """Create a branch from another branch or a commit.

        Parameters
        ----------
        project : str or int
            Project.
        branch : str
            Name of the new branch.
        ref : str
            Branch or commit the new branch starts from.

        Returns
        -------
        dict[str, Any]
            The created branch.
        """
        # Body of the request
        body: dict[str, str] = {"branch": branch, "ref": ref}
        # Create the branch
        created: dict[str, Any] = self.post_json(f"/projects/{encode(project)}/repository/branches", body) or {}
        # Created branch
        return created

    def create_commit(
        self, project: str | int, branch: str, message: str, actions: list[dict[str, str]]
    ) -> dict[str, Any]:
        """Create a commit on an existing branch.

        Parameters
        ----------
        project : str or int
            Project.
        branch : str
            Existing branch that receives the commit.
        message : str
            Commit message.
        actions : list[dict[str, str]]
            File actions (action, file_path, content).

        Returns
        -------
        dict[str, Any]
            The created commit.
        """
        # Body of the request
        body: dict[str, Any] = {"branch": branch, "commit_message": message, "actions": actions}
        # Create the commit
        commit: dict[str, Any] = self.post_json(f"/projects/{encode(project)}/repository/commits", body) or {}
        # Created commit
        return commit

    def create_merge_request(
        self, project: str | int, source_branch: str, target_branch: str, title: str, description: str
    ) -> dict[str, Any]:
        """Open a merge request.

        Parameters
        ----------
        project : str or int
            Project.
        source_branch : str
            Branch with the fix.
        target_branch : str
            Branch to merge into.
        title : str
            Title; "Draft: " keeps it as a draft.
        description : str
            Description in Markdown.

        Returns
        -------
        dict[str, Any]
            The created merge request.
        """
        # Body of the request; the source branch is removed after the merge
        body: dict[str, Any] = {
            "source_branch": source_branch,
            "target_branch": target_branch,
            "title": title,
            "description": description,
            "remove_source_branch": True,
        }
        # Create the merge request
        merge_request: dict[str, Any] = self.post_json(f"/projects/{encode(project)}/merge_requests", body) or {}
        # Created merge request
        return merge_request

    def list_open_issues(self, project: str | int, label: str) -> list[dict[str, Any]]:
        """Return the open issues of a project that carry a label.

        Parameters
        ----------
        project : str or int
            Project.
        label : str
            Label of the issues.

        Returns
        -------
        list[dict[str, Any]]
            Open issues, newest first.
        """
        # Open issues with the label
        params: dict[str, Any] = {"state": "opened", "labels": label, "per_page": 100}
        # Read the issues
        return list(self.get_json(f"/projects/{encode(project)}/issues", params))

    def create_issue(self, project: str | int, title: str, description: str, labels: list[str]) -> dict[str, Any]:
        """Open an issue.

        Parameters
        ----------
        project : str or int
            Project.
        title : str
            Title.
        description : str
            Description in Markdown.
        labels : list[str]
            Labels of the issue.

        Returns
        -------
        dict[str, Any]
            The created issue.
        """
        # Body of the request; GitLab expects the labels comma-separated
        body: dict[str, Any] = {"title": title, "description": description, "labels": ",".join(labels)}
        # Create the issue
        issue: dict[str, Any] = self.post_json(f"/projects/{encode(project)}/issues", body) or {}
        # Created issue
        return issue

    def add_issue_note(self, project: str | int, issue_iid: int, body: str) -> dict[str, Any]:
        """Add a comment to an issue.

        Parameters
        ----------
        project : str or int
            Project.
        issue_iid : int
            Number of the issue in the project.
        body : str
            Comment in Markdown.

        Returns
        -------
        dict[str, Any]
            The created comment.
        """
        # Create the comment
        note: dict[str, Any] = (
            self.post_json(f"/projects/{encode(project)}/issues/{issue_iid}/notes", {"body": body}) or {}
        )
        # Created comment
        return note
