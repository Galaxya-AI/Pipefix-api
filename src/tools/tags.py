"""Tool tags: they decide which agents receive which tools.

A connector tags its tools; an agent lists the tags it needs (tool_tags) and receives the matching tools.
A new tag must be added to KNOWN_TAGS first.
"""

from __future__ import annotations

from collections.abc import Iterable

TAG_CI = "ci"
TAG_CODE = "code"
TAG_OBSERVABILITY = "observability"
# Any other information useful for the diagnosis (quality, dependencies, cluster...)
TAG_CONTEXT = "context"
# Reserved for the fixer
TAG_EDIT = "edit"
TAG_REVIEW = "review"
TAG_RESEARCH = "research"

KNOWN_TAGS = frozenset({TAG_CI, TAG_CODE, TAG_OBSERVABILITY, TAG_CONTEXT, TAG_EDIT, TAG_REVIEW, TAG_RESEARCH})


def check_tags(tags: Iterable[str]) -> frozenset[str]:
    """Raise ValueError on an unknown tag: a typo would otherwise silently give an agent no tools."""
    checked = frozenset(tags)
    unknown = sorted(checked - KNOWN_TAGS)
    if unknown:
        raise ValueError(f"Unknown tool tags {unknown}. Known tags: {sorted(KNOWN_TAGS)}.")
    return checked
