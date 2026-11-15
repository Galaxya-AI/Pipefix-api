"""What the visitor types: the address of a GitLab project and an optional pipeline number."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

from flask import abort

from connectors.errors.errors import ConnectorError
from connectors.gitlab.client import GitLabClient

PIPELINE_IN_URL = re.compile(r"/-/pipelines/(\d+)")


def parse_project_url(address: str) -> tuple[str, str, int | None]:
    """Split a GitLab project address into (instance URL, project path, pipeline number or None).

    Raises:
        ValueError: not an https address of a GitLab project (with the reason, shown to the visitor).
    """
    parts = urlsplit(address.strip())
    if parts.scheme != "https" or not parts.netloc:
        raise ValueError("The project address must start with https://, for example https://gitlab.com/group/project.")
    path = parts.path
    match = PIPELINE_IN_URL.search(path)
    pipeline_id = int(match.group(1)) if match else None
    # GitLab pages of a project live under "/-/": keep only the project part
    path = path.split("/-/", 1)[0].strip("/").removesuffix(".git")
    segments = [segment for segment in path.split("/") if segment]
    if len(segments) < 2:
        raise ValueError("Give the address of a project, for example https://gitlab.com/group/project.")
    return f"https://{parts.netloc}", "/".join(segments), pipeline_id


def read_pipeline_number(value: Any) -> int | None:
    """Return the pipeline number given by the visitor ("3005959635" or "#3005959635"), None when empty.

    Anything else is refused: a number typed wrongly must never fall back to the latest failed pipeline.
    """
    if value is None or value == "":
        return None
    text = str(value).strip().lstrip("#").strip()
    if not text.isdigit() or int(text) < 1:
        abort(400, description="The pipeline number must be a number, for example 2978771 or #2978771.")
    return int(text)


def check_pipeline_to_fix(gitlab: GitLabClient, project: str, pipeline_id: int) -> None:
    """Answer at once, before any agent runs, when the pipeline is missing or has nothing to fix."""
    try:
        pipeline = gitlab.get_pipeline(project, pipeline_id)
    except ConnectorError as error:
        if error.status_code == 404:
            abort(400, description=f"Pipeline #{pipeline_id} does not exist in the project {project}.")
        raise
    status = str(pipeline.get("status", ""))
    if status == "success":
        abort(400, description=f"Pipeline #{pipeline_id} passed: there is nothing to fix.")
    if status in ("created", "pending", "running", "waiting_for_resource", "preparing", "scheduled"):
        abort(400, description=f"Pipeline #{pipeline_id} is still running: try again once it has failed.")
    if status != "failed":
        abort(400, description=f"Pipeline #{pipeline_id} did not fail (status: {status}): there is nothing to fix.")
