"""Task messages: the user message given to each agent.

Every agent receives the same pipeline context, followed by the results of
the previous agents that it needs.
"""

from __future__ import annotations

import json
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


def fix_task(context: str, diagnosis: AgentOutput, issues: list[str]) -> str:
    """`issues` are those of the previous review; empty on the first attempt."""
    task = f"{context}\n\n## Diagnosis\n{as_json(diagnosis)}"
    if issues:
        task += "\n\n## Issues of the previous review (to address)\n" + "\n".join(f"- {item}" for item in issues)
    return task


def review_task(context: str, diagnosis: AgentOutput, fix: AgentOutput, diff: str) -> str:
    return (
        f"{context}\n\n## Diagnosis\n{as_json(diagnosis)}\n\n## Fix summary\n{as_json(fix)}"
        f"\n\n## Proposed diff\n```diff\n{diff}```"
    )
