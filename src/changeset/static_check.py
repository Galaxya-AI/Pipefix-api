"""Static check of a proposed change: Python errors the change adds, found without running the code.

The reviewer is an LLM and can miss a name used after its import was removed. pyflakes finds
it in a few milliseconds; only the problems that the change adds are reported, so problems
already in the file before the change do not block the fix.
"""

from __future__ import annotations

import re
from collections import Counter

from pyflakes.api import check
from pyflakes.reporter import Reporter

# "path:12:5: undefined name 'X'" -> "undefined name 'X'": positions move when lines are added
POSITION_PATTERN = re.compile(r"^[^:]*:\d+:(?:\d+:?)?\s*")


class CollectingReporter(Reporter):  # type: ignore[misc]
    def __init__(self) -> None:
        self.messages: list[str] = []

    def unexpectedError(self, filename: str, message: str) -> None:  # noqa: N802 (pyflakes API)
        self.messages.append(f"{filename}: {message}")

    def syntaxError(self, filename: str, message: str, lineno: int, offset: int, text: str) -> None:  # noqa: N802
        self.messages.append(f"{filename}:{lineno}: syntax error: {message}")

    def flake(self, message: object) -> None:
        self.messages.append(str(message))


def pyflakes_messages(path: str, source: str) -> list[str]:
    reporter = CollectingReporter()
    check(source, path, reporter)
    return reporter.messages


def without_position(message: str) -> str:
    return POSITION_PATTERN.sub("", message)


def added_problems(files: dict[str, tuple[str, str]]) -> list[str]:
    """Return the pyflakes problems of the changed Python files that the change adds, with their line."""
    problems: list[str] = []
    for path, (before, after) in sorted(files.items()):
        if not path.endswith(".py") or before == after:
            continue
        known = Counter(without_position(message) for message in pyflakes_messages(path, before))
        for message in pyflakes_messages(path, after):
            key = without_position(message)
            if known[key] > 0:
                known[key] -= 1
                continue
            problems.append(message)
    return problems
