"""Pipeline selection: finds the pipeline to process and refuses the ones the system does not handle.

Refused pipelines: not failed, on a fix branch created by the system (to
avoid loops), or without a branch to fix (merge request or tag pipelines).
"""

from __future__ import annotations

import os
import re
from typing import Any

from workflow_remediation.ports import CodePlatform

# Pipelines of branches with this prefix are never processed: they are the system's own fix branches
FIX_BRANCH_PREFIX = os.getenv("FIX_BRANCH_PREFIX", "auto-fix/")


def latest_failed_pipeline(platform: CodePlatform, project: str) -> dict[str, Any] | None:
    """Return the latest failure still to fix outside the fix branches, or None.

    A failure is already fixed when a newer pipeline of the same branch passed: it is skipped.
    """
    pipelines = platform.list_pipelines(project, per_page=50)
    passed_branches: set[str] = set()
    # Newest first: a success seen before a failure of the same branch is newer than it.
    for pipeline in pipelines:
        branch = str(pipeline.get("ref", ""))
        if branch.startswith(FIX_BRANCH_PREFIX):
            continue
        if pipeline.get("status") == "success":
            passed_branches.add(branch)
        elif pipeline.get("status") == "failed" and branch not in passed_branches:
            return pipeline
    return None


def find_pipeline(platform: CodePlatform, project: str, pipeline_id: int | None) -> dict[str, Any] | None:
    """Return the requested pipeline, or the latest failed one when no id is given."""
    if pipeline_id:
        return platform.get_pipeline(project, pipeline_id)
    return latest_failed_pipeline(platform, project)


def origin_pipeline_id(branch: str) -> int | None:
    """Return the number of the pipeline a fix branch was made for (auto-fix/pipeline-42-...), or None."""
    if not branch.startswith(FIX_BRANCH_PREFIX):
        return None
    found = re.match(r"pipeline-(\d+)(?:-|$)", branch[len(FIX_BRANCH_PREFIX) :])
    return int(found.group(1)) if found else None


def refusal_status(pipeline: dict[str, Any]) -> str | None:
    """Return why a pipeline is not processed, or None for a failed pipeline on an ordinary branch.

    A failed pipeline of a PipeFix fix branch is accepted when it is asked for by number: the
    analysis then resumes on that branch. It is never chosen automatically (latest_failed_pipeline)
    nor by the webhook, to avoid loops.
    """
    branch = str(pipeline.get("ref", ""))
    if pipeline.get("status") != "failed":
        return f"pipeline_{pipeline.get('status')}"
    if branch.startswith(FIX_BRANCH_PREFIX) and origin_pipeline_id(branch) is None:
        return "fix_branch_ignored"
    # Merge request or tag pipeline: no branch to fix
    if branch.startswith("refs/") or pipeline.get("tag"):
        return "unsupported_pipeline_type"
    return None
