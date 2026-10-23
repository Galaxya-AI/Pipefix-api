"""Tool tags: they decide which agents receive which tools.

A connector tags its tools; each agent lists the tags it needs in its
configuration (tool_tags) and automatically receives the matching tools.
Only the tags of KNOWN_TAGS are accepted: a new tag must be added here first.
"""

from __future__ import annotations

from collections.abc import Iterable

# Pipelines, jobs and their logs
TAG_CI: str = "ci"

# Reading the source code of the repository
TAG_CODE: str = "code"

# Metrics, alerts and dashboards
TAG_OBSERVABILITY: str = "observability"

# Any other information useful for the diagnosis (quality, dependencies, cluster...)
TAG_CONTEXT: str = "context"

# Proposing changes (reserved for the fixer)
TAG_EDIT: str = "edit"

# Viewing the proposed changes
TAG_REVIEW: str = "review"

# Searching the web for unknown errors (research agent)
TAG_RESEARCH: str = "research"

# Every known tag
KNOWN_TAGS: frozenset[str] = frozenset(
    {TAG_CI, TAG_CODE, TAG_OBSERVABILITY, TAG_CONTEXT, TAG_EDIT, TAG_REVIEW, TAG_RESEARCH}
)


def check_tags(tags: Iterable[str]) -> frozenset[str]:
    """Check that every tag is a known tag.

    A typo in a tag (e.g. "obserability") would silently give an agent no
    tools: this check turns it into a clear error at start-up.

    Parameters
    ----------
    tags : Iterable[str]
        Tags of a tool or of an agent.

    Returns
    -------
    frozenset[str]
        The same tags.

    Raises
    ------
    ValueError
        If a tag is not in KNOWN_TAGS.
    """
    # Tags as a set
    checked: frozenset[str] = frozenset(tags)
    # Tags missing from the known list
    unknown: list[str] = sorted(checked - KNOWN_TAGS)
    # At least one unknown tag
    if unknown:
        # Clear error with the valid tags
        raise ValueError(f"Unknown tool tags {unknown}. Known tags: {sorted(KNOWN_TAGS)}.")
    # Valid tags
    return checked
