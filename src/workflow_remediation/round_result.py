"""Result of one remediation round: the agents' outputs and the proposed change."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agents.outputs import AgentOutput


def dump_output(output: AgentOutput | None) -> dict[str, Any] | None:
    return output.model_dump() if output is not None else None


@dataclass
class RoundResult:
    pipeline_id: int
    # e.g. "fix_ready", "review_rejected"
    status: str = ""
    triage: AgentOutput | None = None
    diagnosis: AgentOutput | None = None
    # Last fixer and reviewer outputs
    fix: AgentOutput | None = None
    review: AgentOutput | None = None
    attempts: int = 0
    diff: str = ""
    # New content of every changed file
    files: dict[str, str] = field(default_factory=dict)
    error: str = ""
    # Key of the failure in the cache of approved fixes (empty when the cache is off)
    reuse_key: str = ""

    def as_report(self) -> dict[str, Any]:
        """Plain data for the report, without the file contents."""
        return {
            "pipeline_id": self.pipeline_id,
            "status": self.status,
            "triage": dump_output(self.triage),
            "diagnosis": dump_output(self.diagnosis),
            "fix": dump_output(self.fix),
            "review": dump_output(self.review),
            "attempts": self.attempts,
            "changed_files": sorted(self.files),
            "error": self.error,
        }
