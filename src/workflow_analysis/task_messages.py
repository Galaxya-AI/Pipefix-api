"""Task messages: the user message given to each agent.

Every agent receives the same pipeline context, followed by the results of
the previous agents that it needs.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any

from agents.outputs import AgentOutput


def as_json(output: AgentOutput) -> str:
    return json.dumps(output.model_dump(), ensure_ascii=False, indent=2)


def pipeline_context(project: str, pipeline: dict[str, Any], jobs: list[dict[str, Any]], logs: str) -> str:
    """Return the failed jobs and bounded log excerpts for the agents."""
    commit = str(pipeline.get("sha", ""))[:8]
    lines = [
        "## Failed pipeline",
        f"Project: {project} - pipeline #{pipeline.get('id')} - branch {pipeline.get('ref')} - commit {commit}",
        "",
        "Failed jobs:",
    ]
    for job in jobs:
        lines.append(
            f'- job {job.get("id")} "{job.get("name")}" (stage {job.get("stage")}, reason: {job.get("failure_reason")})'
        )
    lines += ["", "Failed job log excerpts:", "```", logs, "```"]
    return "\n".join(lines)


def diagnosis_task(context: str, triage: AgentOutput) -> str:
    return f"{context}\n\n## Triage result\n{as_json(triage)}"


def fix_task(context: str, diagnosis: AgentOutput, issues: list[str], files: str = "") -> str:
    """`issues` are those of the previous review; empty on the first attempt.

    `files`: current content of the files the diagnosis names, read by the workflow so the
    fixer does not spend LLM calls reading them.
    """
    task = f"{context}\n\n## Diagnosis\n{as_json(diagnosis)}"
    if files:
        task += f"\n\n## Current content of the files to change (exact, with the changes already proposed)\n{files}"
    if issues:
        task += "\n\n## Issues of the previous review (to address)\n" + "\n".join(f"- {item}" for item in issues)
    return task


def review_task(context: str, diagnosis: AgentOutput, fix: AgentOutput, diff: str, files: str = "") -> str:
    """`files`: full new content of the changed files, so the reviewer can check them without reading them."""
    task = (
        f"{context}\n\n## Diagnosis\n{as_json(diagnosis)}\n\n## Fix summary\n{as_json(fix)}"
        f"\n\n## Proposed diff\n```diff\n{diff}```"
    )
    if files:
        task += f"\n\n## Changed files after the fix (full content)\n{files}"
    return task


def file_contents(read: Callable[[str], str], paths: Iterable[str], max_chars: int, max_files: int) -> str:
    """Return the content of each file as a fenced block; an unreadable file is reported, not raised.

    A file longer than max_chars is cut, with a note asking to read the rest with read_file.
    """
    blocks = []
    for path in list(dict.fromkeys(paths))[:max_files]:
        try:
            content = read(path)
        # e.g. a file the fix must create, or a wrong path in the diagnosis
        except Exception as error:
            blocks.append(f"### {path}\n(could not be read: {type(error).__name__}: {str(error)[:200]})")
            continue
        note = ""
        if max_chars > 0 and len(content) > max_chars:
            content = content[:max_chars]
            note = f"\n(cut after {max_chars} characters: read_file(raw=true) for the rest)"
        blocks.append(f"### {path}\n````\n{content}\n````{note}")
    return "\n\n".join(blocks)
