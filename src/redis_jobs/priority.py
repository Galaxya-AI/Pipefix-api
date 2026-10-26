"""Priority of the runs: failures on the important branches of a project are fixed first.

Each project defines its important branches in GitLab: its default branch and
its protected branches. Nothing has to be configured here. PRIORITY_BRANCHES
(comma-separated glob patterns such as release/*) replaces that rule when set.

Two queues: "high" for the important branches, "default" for the others.
The workers read them in the order given to "rq worker" in docker-compose.yml.
"""

from __future__ import annotations

import fnmatch
import os

from workflow_remediation.ports import CodePlatform

HIGH_QUEUE = "high"

DEFAULT_QUEUE = "default"

# Empty: the default and protected branches of each project, read in GitLab.
PRIORITY_BRANCHES = tuple(item.strip() for item in os.getenv("PRIORITY_BRANCHES", "").split(",") if item.strip())


def queue_for_branch(
    platform: CodePlatform, project: str, branch: str, patterns: tuple[str, ...] = PRIORITY_BRANCHES
) -> str:
    """Return the queue of a run, from the branch of its failed pipeline."""
    if patterns:
        important = any(fnmatch.fnmatchcase(branch, pattern) for pattern in patterns)
    else:
        important = platform.is_protected_branch(project, branch)
    return HIGH_QUEUE if important else DEFAULT_QUEUE
