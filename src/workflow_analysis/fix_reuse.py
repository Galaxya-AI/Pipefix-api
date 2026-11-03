"""Reuse a fix already found for the same failure on the same commit, without any LLM call.

A pipeline is often run again on the same commit: someone presses Retry, or GitLab sends
the webhook again. The code and the errors are the same, so the diagnosis and the fix are
the same too. PipeFix keeps each approved fix under a key made of the project, the commit
and the error lines of the failed jobs, and reuses it when that key comes back.

Only the same commit is reused: on another commit the code changed, and an old fix could be wrong.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from agents.diagnostic_agent import DiagnosisOutput
from agents.fixer_agent import FixOutput
from agents.reviewer import ReviewOutput
from agents.triage import TriageOutput
from startup.env_values import env_bool, env_int
from text_tools.log_cleaning import failure_fingerprint
from workflow_remediation.round_result import RoundResult

REUSE_FIXES = env_bool("REUSE_FIXES", True)
# How long a fix may be reused, in seconds (7 days)
REUSE_TTL_S = env_int("REUSE_TTL_S", 7 * 24 * 3600, minimum=60)
CACHE_FOLDER = "_reused_fixes"


def failure_key(project: str, sha: str, jobs: list[dict[str, Any]], traces: dict[int, str]) -> str:
    """Key of a failure: project, commit, and the error lines of each failed job (without durations or ids)."""
    parts = [project, sha]
    for job in sorted(jobs, key=lambda item: str(item.get("name", ""))):
        trace = traces.get(int(job["id"]), "")
        parts.append(f"{job.get('name')}\n{failure_fingerprint(trace)}")
    return hashlib.sha256("\n\0".join(parts).encode("utf-8")).hexdigest()


class FixCache:
    """Approved fixes on disk, one JSON file per failure key, in the runs folder."""

    def __init__(self, runs_dir: Path, ttl_s: int = REUSE_TTL_S) -> None:
        self.folder = Path(runs_dir) / CACHE_FOLDER
        self.ttl_s = ttl_s

    def path(self, key: str) -> Path:
        return self.folder / f"{key}.json"

    def save(self, key: str, result: RoundResult, run_id: str) -> None:
        """Keep an approved fix; never raises (a cache that cannot be written only costs a new analysis)."""
        data = {
            "saved_at": time.time(),
            "run_id": run_id,
            "triage": result.triage.model_dump() if result.triage else None,
            "diagnosis": result.diagnosis.model_dump() if result.diagnosis else None,
            "fix": result.fix.model_dump() if result.fix else None,
            "review": result.review.model_dump() if result.review else None,
            "files": result.files,
        }
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            self.path(key).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except (OSError, TypeError, ValueError):
            pass

    def forget(self, key: str) -> None:
        try:
            self.path(key).unlink(missing_ok=True)
        except OSError:
            pass

    def load(self, key: str) -> dict[str, Any] | None:
        """The saved fix of this key, or None if there is none, it is too old or it cannot be read."""
        try:
            data = json.loads(self.path(key).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or time.time() - float(data.get("saved_at", 0)) > self.ttl_s:
            return None
        if not data.get("files") or not all(data.get(name) for name in ("triage", "diagnosis", "fix", "review")):
            return None
        return data


def rebuild(data: dict[str, Any], result: RoundResult) -> bool:
    """Fill the round with a saved fix; False if the saved outputs are no longer valid."""
    try:
        result.triage = TriageOutput.model_validate(data["triage"])
        result.diagnosis = DiagnosisOutput.model_validate(data["diagnosis"])
        result.fix = FixOutput.model_validate(data["fix"])
        result.review = ReviewOutput.model_validate(data["review"])
    except (ValueError, KeyError, TypeError):
        return False
    return True
