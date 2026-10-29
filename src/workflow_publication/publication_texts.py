"""Names and texts written to GitLab when a fix is published (pure functions, no GitLab call)."""

from __future__ import annotations

import os
from typing import Any

from text_tools.log_cleaning import truncate
from workflow_remediation.round_result import RoundResult

# Pipelines of branches with this prefix are never processed: they are the system's own fix branches
FIX_BRANCH_PREFIX = os.getenv("FIX_BRANCH_PREFIX", "auto-fix/")


def fix_branch_name(pipeline_id: int) -> str:
    """Return e.g. "auto-fix/pipeline-42"."""
    return f"{FIX_BRANCH_PREFIX}pipeline-{pipeline_id}"


def text_field(output: Any, name: str, default: str = "") -> str:
    """Return a field of an agent output (possibly None) as text, or `default` when missing."""
    return str(getattr(output, name, default) if output is not None else default)


def merge_request_description(pipeline: dict[str, Any], result: RoundResult, base_moved: bool = False) -> str:
    """Return the Markdown description; `base_moved` means the target got commits after the analysed one."""
    link = str(pipeline.get("web_url") or f"#{pipeline.get('id')}")
    tests_warning = (
        "**Warning: test files were changed.** Check that no test was weakened.\n"
        if getattr(result.fix, "tests_modified", False)
        else ""
    )
    base_warning = (
        f"**Warning: `{pipeline.get('ref')}` has new commits since the analysed commit "
        f"`{str(pipeline.get('sha', ''))[:8]}`.** The fix branch starts from the analysed commit: "
        "check the merge request for conflicts before merging.\n"
        if base_moved
        else ""
    )
    return "\n".join(
        [
            "## Fix proposed by the CI remediation system",
            "",
            f"Original pipeline: {link} (analysed commit `{str(pipeline.get('sha', ''))[:8]}`)",
            "",
            base_warning,
            f"**Triage**: `{text_field(result.triage, 'category')}` - {text_field(result.triage, 'summary')}",
            "",
            f"**Root cause** ({text_field(result.diagnosis, 'confidence', '?')}): "
            f"{text_field(result.diagnosis, 'root_cause')}",
            "",
            f"**Fix**: {text_field(result.fix, 'summary')}",
            "",
            tests_warning,
            f"**Automatic review**: risk {text_field(result.review, 'risk', '?')} - "
            f"{text_field(result.review, 'comment')}",
            "",
            # No copy of the diff here: GitLab already shows it in the Changes tab.
            "> Merge request created automatically: review it before merging.",
        ]
    )


def commit_message(result: RoundResult) -> str:
    # Git convention: a title line of at most 72 characters
    title = text_field(result.fix, "summary", "automatic fix")[:72]
    return (
        f"fix: {title}\n\nRoot cause: {text_field(result.diagnosis, 'root_cause', 'not given')}\n\n"
        "Proposed automatically by the CI remediation system. Review before merging."
    )


def commit_actions(result: RoundResult) -> list[dict[str, str]]:
    """Return the GitLab commit actions: one "update" per changed file."""
    return [{"action": "update", "file_path": path, "content": content} for path, content in result.files.items()]


def failure_issue_title(branch: str) -> str:
    """Same title for every failure of a branch: later failures are added as comments to one issue."""
    return f"CI remediation: pipeline failure not fixed on {branch}"


def failure_report_text(report: dict[str, Any]) -> str:
    """Return a Markdown summary of a run that did not fix the pipeline."""
    pipeline = report.get("pipeline") or {}
    last_round = (report.get("rounds") or [{}])[-1]
    diagnosis = last_round.get("diagnosis") or {}
    fix = last_round.get("fix") or {}
    review = last_round.get("review") or {}
    lines = [
        f"**Status**: `{report.get('status')}` - run `{report.get('run_id')}`",
        f"**Pipeline**: {pipeline.get('url') or pipeline.get('id') or 'unknown'} (branch `{pipeline.get('branch')}`)",
    ]
    if diagnosis.get("root_cause"):
        lines.append(f"**Root cause found** ({diagnosis.get('confidence', '?')}): {diagnosis['root_cause']}")
    if fix.get("summary"):
        lines.append(f"**Fix tried** ({last_round.get('attempts', 0)} attempt(s)): {fix['summary']}")
    if review.get("issues"):
        lines.append("**Review issues**:\n" + "\n".join(f"- {item}" for item in review["issues"]))
    error = str(report.get("error") or last_round.get("error") or "")
    if error:
        lines.append(f"**Error**: {truncate(error, 1000)}")
    lines.append("_A human must look at this failure. Details: runs/<run_id>/report.json and journal.jsonl._")
    return "\n\n".join(lines)
