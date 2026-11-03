"""Earlier fix attempts on a PipeFix branch, given to the agents so they do not repeat one.

Without it, the diagnosis sees the latest PipeFix commit in the history, takes it for the
cause and proposes to revert it, which brings back code that already failed. Two sources,
both read without an LLM call:
- the rounds of the current run: the diff of each committed fix and the errors after it;
- for a resumed fix branch, its earlier failed pipelines: each commit is an earlier attempt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from text_tools.log_cleaning import error_summary, strip_ansi, truncate

FIX_BRANCH_PREFIX = "auto-fix/"
# Kept short: the history is added to the task of every agent
DIFF_MAX_CHARS = 3000
ERRORS_PER_ATTEMPT = 10
MAX_EARLIER_PIPELINES = 10


@dataclass
class Attempt:
    """A fix committed by a round of this run, and the errors of the pipeline that ran on it."""

    round: int
    commit: str
    diff: str
    errors_after: list[str] = field(default_factory=list)


def errors_of(traces: dict[int, str]) -> list[str]:
    """The first error lines of the failed jobs, to say what still failed after an attempt."""
    lines: list[str] = []
    for trace in traces.values():
        for line in error_summary(strip_ansi(trace), ERRORS_PER_ATTEMPT):
            if line not in lines:
                lines.append(line)
    return lines[:ERRORS_PER_ATTEMPT]


def earlier_pipelines(platform: Any, project: str, pipeline: dict[str, Any]) -> list[dict[str, Any]]:
    """Failed pipelines of a PipeFix branch before `pipeline`, newest first (empty for another branch)."""
    branch = str(pipeline.get("ref", ""))
    if not branch.startswith(FIX_BRANCH_PREFIX):
        return []
    try:
        pipelines = platform.list_pipelines(project, ref=branch, per_page=MAX_EARLIER_PIPELINES + 1)
    # The history is a help: without it the round still runs
    except Exception:
        return []
    if not isinstance(pipelines, list):
        return []
    return [item for item in pipelines if item.get("status") == "failed" and item.get("id") != pipeline.get("id")][
        :MAX_EARLIER_PIPELINES
    ]


def history_text(pipeline: dict[str, Any], earlier: list[dict[str, Any]], attempts: list[Attempt]) -> str:
    """Text added to the context of every agent; empty when there is no earlier attempt."""
    branch = str(pipeline.get("ref", ""))
    if not attempts and not branch.startswith(FIX_BRANCH_PREFIX):
        return ""
    parts = [
        "## Earlier fix attempts (none made the pipeline pass)",
        "Rules: the code before an attempt may fail too, so do not just revert the latest commit; "
        "never restore code that an earlier attempt already had; find a fix that differs from all of them.",
    ]
    if branch.startswith(FIX_BRANCH_PREFIX):
        parts.append(
            f"The analysed branch {branch} is a PipeFix fix branch: its recent commits are earlier "
            "PipeFix attempts, not the cause of the failure."
        )
    if earlier:
        parts.append(
            "Earlier failed pipelines of this branch (get_commit_changes shows what each commit tried):\n"
            + "\n".join(f"- pipeline #{item.get('id')}: commit {str(item.get('sha', ''))[:8]}" for item in earlier)
        )
    for attempt in attempts:
        errors = "\n".join(f"  {line}" for line in attempt.errors_after) or "  (errors of the current pipeline)"
        parts.append(
            f"### Round {attempt.round} of this run: commit {attempt.commit[:8]}\n"
            f"```diff\n{truncate(attempt.diff, DIFF_MAX_CHARS)}\n```\nStill failing after it:\n{errors}"
        )
    return "\n\n".join(parts)
