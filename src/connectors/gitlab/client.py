"""GitLab REST API (v4) client, for gitlab.com or a self-hosted instance.

Only HTTP calls, no agent logic. Used by the GitLab connector for the read-only agent
tools, and by the workflow to read pipelines and publish fixes (branch, commit and
merge request), steps that are kept out of the LLM's reach.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import requests

from connectors.errors.errors import ConnectorError
from connectors.http_client.http_api import HttpApi

# Statuses GitLab returns when an anonymous caller may not use the jobs API.
ANONYMOUS_REFUSALS = frozenset({401, 403})


def encode(value: str | int) -> str:
    """Encode a project, a file path or a branch for a URL, "/" included as GitLab expects."""
    return quote(str(value), safe="")


class GitLabClient(HttpApi):
    def __init__(self, base_url: str, token: str = "", timeout_s: int = 30) -> None:
        # No token: anonymous calls, enough to read a public project whose pipelines are public.
        headers = {"PRIVATE-TOKEN": token} if token else {}
        self.web_url = base_url.rstrip("/")
        self.has_token = bool(token)
        super().__init__(f"{self.web_url}/api/v4", headers, timeout_s)

    def get_project(self, project: str | int) -> dict[str, Any]:
        project_data: dict[str, Any] = self.get_json(f"/projects/{encode(project)}")
        return project_data

    def get_pipeline(self, project: str | int, pipeline_id: int) -> dict[str, Any]:
        pipeline: dict[str, Any] = self.get_json(f"/projects/{encode(project)}/pipelines/{pipeline_id}")
        return pipeline

    def list_pipelines(self, project: str | int, **filters: Any) -> list[dict[str, Any]]:
        """Return pipelines newest first; filters are GitLab query parameters (status, ref, sha, per_page...)."""
        params = {"order_by": "id", "sort": "desc", **filters}
        return list(self.get_json(f"/projects/{encode(project)}/pipelines", params))

    def pipeline_jobs(self, project: str | int, pipeline_id: int, scope: str | None = None) -> list[dict[str, Any]]:
        """Return up to 100 jobs of a pipeline, optionally filtered by status (e.g. "failed")."""
        params: dict[str, Any] = {"per_page": 100}
        if scope:
            params["scope[]"] = scope
        return list(self.get_json(f"/projects/{encode(project)}/pipelines/{pipeline_id}/jobs", params))

    def job_trace(self, project: str | int, job_id: int) -> str:
        """Return the log of a job.

        gitlab.com refuses the jobs API to anonymous callers, even on a public project. Without a
        token, the public log page of the job is read instead, as a browser would.
        """
        try:
            return self.get_text(f"/projects/{encode(project)}/jobs/{job_id}/trace")
        except ConnectorError as error:
            if self.has_token or error.status_code not in ANONYMOUS_REFUSALS:
                raise
        return self.public_job_log(project, job_id)

    def public_job_log(self, project: str | int, job_id: int) -> str:
        """Read the raw log page of a job of a public project, without a token."""
        path = str(project)
        if path.isdigit():
            path = str(self.get_project(project)["path_with_namespace"])
        url = f"{self.web_url}/{path}/-/jobs/{job_id}/raw"
        refused = ConnectorError(
            f"The log of job {job_id} is not public: this project needs an access token with the read_api scope.",
            401,
        )
        try:
            # No redirect: a private log sends the caller to the sign-in page.
            response = self.session.get(url, timeout=self.timeout_s, allow_redirects=False)
        except requests.RequestException as error:
            raise ConnectorError(f"GET {url}: network error ({error})") from error
        if response.status_code in ANONYMOUS_REFUSALS or 300 <= response.status_code < 400:
            raise refused
        if response.status_code >= 400:
            raise ConnectorError(f"GET {url} -> {response.status_code}", response.status_code)
        if "text/html" in response.headers.get("Content-Type", ""):
            raise refused
        response.encoding = response.encoding or "utf-8"
        return response.text

    def retry_job(self, project: str | int, job_id: int) -> dict[str, Any]:
        job: dict[str, Any] = self.post_json(f"/projects/{encode(project)}/jobs/{job_id}/retry") or {}
        return job

    def list_tree(self, project: str | int, ref: str, max_pages: int = 50) -> list[dict[str, Any]]:
        """Return the full recursive repository tree, reading at most max_pages pages of 100 entries."""
        items: list[dict[str, Any]] = []
        page = 1
        while page <= max_pages:
            response = self.request(
                "GET",
                f"/projects/{encode(project)}/repository/tree",
                params={"ref": ref, "recursive": "true", "per_page": 100, "page": page},
            )
            items.extend(response.json())
            # Empty on the last page
            next_page = response.headers.get("X-Next-Page", "")
            if not next_page:
                break
            page = int(next_page)
        return items

    def file_raw(self, project: str | int, path: str, ref: str) -> str:
        return self.get_text(f"/projects/{encode(project)}/repository/files/{encode(path)}/raw", {"ref": ref})

    def recent_commits(self, project: str | int, ref: str, limit: int = 10) -> list[dict[str, Any]]:
        return list(
            self.get_json(f"/projects/{encode(project)}/repository/commits", {"ref_name": ref, "per_page": limit})
        )

    def commit_diff(self, project: str | int, sha: str) -> list[dict[str, Any]]:
        return list(self.get_json(f"/projects/{encode(project)}/repository/commits/{encode(sha)}/diff"))

    def branch_exists(self, project: str | int, branch: str) -> bool:
        """Raise ConnectorError on any error other than "not found"."""
        try:
            self.get_json(f"/projects/{encode(project)}/repository/branches/{encode(branch)}")
        except ConnectorError as error:
            if error.status_code == 404:
                return False
            raise
        return True

    def is_protected_branch(self, project: str | int, branch: str) -> bool:
        """True for the default branch or a protected branch (patterns such as release/* included)."""
        try:
            data = self.get_json(f"/projects/{encode(project)}/repository/branches/{encode(branch)}")
        except ConnectorError as error:
            if error.status_code == 404:
                return False
            raise
        return bool(data.get("protected") or data.get("default"))

    def branch_head(self, project: str | int, branch: str) -> str:
        """Return the id of the latest commit of a branch, empty if GitLab gives none."""
        data = self.get_json(f"/projects/{encode(project)}/repository/branches/{encode(branch)}")
        return str((data.get("commit") or {}).get("id", ""))

    def create_branch(self, project: str | int, branch: str, ref: str) -> dict[str, Any]:
        body = {"branch": branch, "ref": ref}
        created: dict[str, Any] = self.post_json(f"/projects/{encode(project)}/repository/branches", body) or {}
        return created

    def create_commit(
        self, project: str | int, branch: str, message: str, actions: list[dict[str, str]]
    ) -> dict[str, Any]:
        """Create a commit on an existing branch; actions hold action, file_path and content."""
        body = {"branch": branch, "commit_message": message, "actions": actions}
        commit: dict[str, Any] = self.post_json(f"/projects/{encode(project)}/repository/commits", body) or {}
        return commit

    def find_merge_request(self, project: str | int, source_branch: str) -> dict[str, Any] | None:
        """Return the open merge request of a branch, or None."""
        params = {"state": "opened", "source_branch": source_branch, "per_page": 1}
        found = list(self.get_json(f"/projects/{encode(project)}/merge_requests", params))
        return found[0] if found else None

    def create_merge_request(
        self, project: str | int, source_branch: str, target_branch: str, title: str, description: str
    ) -> dict[str, Any]:
        """Open a merge request; a "Draft: " title keeps it as a draft."""
        body = {
            "source_branch": source_branch,
            "target_branch": target_branch,
            "title": title,
            "description": description,
            "remove_source_branch": True,
        }
        merge_request: dict[str, Any] = self.post_json(f"/projects/{encode(project)}/merge_requests", body) or {}
        return merge_request

    def list_open_issues(self, project: str | int, label: str) -> list[dict[str, Any]]:
        params = {"state": "opened", "labels": label, "per_page": 100}
        return list(self.get_json(f"/projects/{encode(project)}/issues", params))

    def create_issue(self, project: str | int, title: str, description: str, labels: list[str]) -> dict[str, Any]:
        # GitLab expects the labels comma-separated.
        body = {"title": title, "description": description, "labels": ",".join(labels)}
        issue: dict[str, Any] = self.post_json(f"/projects/{encode(project)}/issues", body) or {}
        return issue

    def add_issue_note(self, project: str | int, issue_iid: int, body: str) -> dict[str, Any]:
        note: dict[str, Any] = (
            self.post_json(f"/projects/{encode(project)}/issues/{issue_iid}/notes", {"body": body}) or {}
        )
        return note
