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


# Line number of a pyflakes message: "path:12:5: ..." or "path:12: syntax error: ..."
LINE_PATTERN = re.compile(r"^[^:]*:(\d+):")


def with_lines(message: str, source: str, around: int = 1) -> str:
    """Add the code lines around the line a message names, so the fixer sees what to repair."""
    found = LINE_PATTERN.match(message)
    if not found:
        return message
    number = int(found.group(1))
    lines = source.splitlines()
    first, last = max(1, number - around), min(len(lines), number + around)
    shown = [f"    {index:>4} | {lines[index - 1]}" for index in range(first, last + 1)]
    return "\n".join([message, *shown])


def added_problems_with_lines(files: dict[str, tuple[str, str]]) -> list[str]:
    """Like added_problems, each problem followed by the code lines around it."""
    problems: list[str] = []
    for path, (before, after) in sorted(files.items()):
        problems.extend(with_lines(problem, after) for problem in added_problems({path: (before, after)}))
    return problems
