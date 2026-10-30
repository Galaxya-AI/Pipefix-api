"""Independent CI validation policy: never equate an agent review with passing CI."""
from __future__ import annotations

from typing import Any


def failed_job_signature(jobs: list[dict[str, Any]]) -> tuple[str, ...]:
    """Stable approximation of failure identity across GitLab pipeline IDs."""
    return tuple(sorted(str(j.get('name', j.get('id', 'unknown'))) for j in jobs if not j.get('allow_failure')))


def validation_record(pipeline: dict[str, Any] | None, expected_sha: str) -> dict[str, Any]:
    """A success is verified only when it is for the newly committed SHA."""
    if pipeline is None:
        return {"verified": False, "reason": "pipeline_timeout", "pipeline_id": None}
    actual_sha = str(pipeline.get('sha') or '')
    status = str(pipeline.get('status') or '')
    verified = bool(expected_sha) and actual_sha == expected_sha and status == 'success'
    return {
        "verified": verified,
        "reason": "success" if verified else ("sha_mismatch" if actual_sha != expected_sha else status or "unknown"),
        "pipeline_id": pipeline.get('id'),
        "pipeline_url": pipeline.get('web_url'),
        "commit_sha": actual_sha,
    }
