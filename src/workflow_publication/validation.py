"""Independent CI validation policy: never equate an agent review with passing CI."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from text_tools.log_cleaning import failure_fingerprint


def failed_job_signature(jobs: list[dict[str, Any]], traces: Mapping[int, str] | None = None) -> tuple[str, ...]:
    """Identify mandatory failures across pipelines, including cleaned log tails when available."""
    signatures = []

    for job in jobs:
        if job.get("allow_failure"):
            continue

        name = str(job.get("name", job.get("id", "unknown")))

        if traces is not None:
            # The error lines of the whole log, without durations or ids: two runs of the same
            # error match, a new error hidden until now by the previous one does not.
            trace = failure_fingerprint(traces.get(int(job["id"]), ""))

            digest = hashlib.sha256(trace.encode("utf-8")).hexdigest()
            name = f"{name}:{job.get('failure_reason') or ''}:{digest}"

        signatures.append(name)

    return tuple(sorted(signatures))


def validation_record(pipeline: dict[str, Any] | None, expected_sha: str) -> dict[str, Any]:
    """A success is verified only when it is for the newly committed SHA."""
    if pipeline is None:
        return {"verified": False, "reason": "pipeline_timeout", "pipeline_id": None}
    actual_sha = str(pipeline.get("sha") or "")
    status = str(pipeline.get("status") or "")
    verified = bool(expected_sha) and actual_sha == expected_sha and status == "success"
    return {
        "verified": verified,
        "reason": "success" if verified else ("sha_mismatch" if actual_sha != expected_sha else status or "unknown"),
        "pipeline_id": pipeline.get("id"),
        "pipeline_url": pipeline.get("web_url"),
        "commit_sha": actual_sha,
    }
