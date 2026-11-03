"""Code of the failed tests, read without an LLM call.

A pytest summary line ("FAILED tests/test_x.py::test_y - AssertionError: ...") shows the
assertion, not the call that produced it. The agents get the source of each failed test
function, so they see the exact inputs and expected values instead of guessing them.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Iterable

# "FAILED tests/test_x.py::TestClass::test_y[param] - message" -> path, node names
FAILED_TEST_PATTERN = re.compile(r"^(?:FAILED|ERROR) (?P<path>[^\s:]+\.py)::(?P<nodes>[^\s\[]+)")


def failed_tests(lines: Iterable[str]) -> list[tuple[str, list[str]]]:
    """Return (file path, [class..., function]) for each failed test, in order, without duplicates."""
    found: list[tuple[str, list[str]]] = []
    for line in lines:
        match = FAILED_TEST_PATTERN.match(line.strip())
        if not match:
            continue
        test = (match.group("path"), match.group("nodes").split("::"))
        if test not in found:
            found.append(test)
    return found


def function_source(source: str, nodes: list[str]) -> str | None:
    """Return the source of a test function (with its decorators), or None if it is not found."""
    try:
        body: list[ast.stmt] = ast.parse(source).body
    except SyntaxError:
        return None
    node: ast.AST | None = None
    for name in nodes:
        node = next(
            (
                item
                for item in body
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and item.name == name
            ),
            None,
        )
        if node is None:
            return None
        body = node.body if isinstance(node, ast.ClassDef) else []
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    lines = source.splitlines()
    first = min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)])
    last = node.end_lineno or node.lineno
    return "\n".join(lines[first - 1 : last])


def failed_test_sources(
    read: Callable[[str], str], lines: Iterable[str], max_chars: int = 12000, max_tests: int = 20
) -> str:
    """Return the code of the failed tests as fenced blocks (empty if none is found).

    Each file is read once; a test that cannot be read or found is skipped.
    """
    files: dict[str, str | None] = {}
    blocks: list[str] = []
    used = 0
    for path, nodes in failed_tests(lines)[:max_tests]:
        if path not in files:
            try:
                files[path] = read(path)
            # e.g. a test file deleted since, or a path outside the repository
            except Exception:
                files[path] = None
        content = files[path]
        code = function_source(content, nodes) if content is not None else None
        if code is None:
            continue
        block = f"### {path}::{'::'.join(nodes)}\n```python\n{code}\n```"
        if max_chars > 0 and used + len(block) > max_chars:
            blocks.append("(more failed tests: read their file with read_file)")
            break
        blocks.append(block)
        used += len(block)
    return "\n\n".join(blocks)
