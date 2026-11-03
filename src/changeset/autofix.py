"""Lint and format errors fixed by ruff itself, without an LLM call.

Many failed lint jobs only report errors that ruff can fix on its own: an unused import,
an unsorted import block, a file to reformat. PipeFix runs ruff on exactly the files and
rules the log names, with the project's own ruff configuration, before asking any agent.
When this fixes every error of every failed job, no agent is needed; otherwise the agents
get the files already fixed and only the remaining errors to handle.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from startup.env_values import env_bool, env_int
from text_tools.log_cleaning import error_summary, strip_ansi

AUTOFIX_RUFF = env_bool("AUTOFIX_RUFF", True)
RUFF_TIMEOUT_S = env_int("RUFF_TIMEOUT_S", 60, minimum=5)

# "src/app.py:3:8: F401 [*] `os` imported but unused" (ruff check, concise output)
RUFF_ERROR = re.compile(r"^(?P<path>[^\s:]+\.py):\d+:\d+: (?P<code>[A-Z]+\d+)\b")
# "Would reformat: src/app.py" (ruff format --check)
RUFF_FORMAT = re.compile(r"^Would reformat: (?P<path>\S+\.py)$")
# Summary lines of ruff that are not errors of their own
RUFF_SUMMARY = re.compile(r"^(?:Found \d+ errors?|\d+ files? would be reformatted|\[\*\] \d+ fixable)")

# Files where the project keeps its ruff configuration, copied next to the files to fix
CONFIG_FILES = ("pyproject.toml", "ruff.toml", ".ruff.toml")


@dataclass
class LintReport:
    """What the failed jobs say that ruff can fix."""

    codes: set[str] = field(default_factory=set)
    check_paths: set[str] = field(default_factory=set)
    format_paths: set[str] = field(default_factory=set)
    # True when every error line of every failed job is one ruff can handle
    only_ruff: bool = True

    def empty(self) -> bool:
        return not self.check_paths and not self.format_paths


@dataclass
class AutofixResult:
    files: dict[str, str] = field(default_factory=dict)
    # Errors ruff still reports on the fixed files (for the reviewed rules)
    remaining: list[str] = field(default_factory=list)
    report: LintReport = field(default_factory=LintReport)

    def complete(self) -> bool:
        """True when ruff alone fixed every error of every failed job."""
        return bool(self.files) and self.report.only_ruff and not self.remaining


def safe_path(path: str) -> bool:
    """A relative path that stays in the repository."""
    return bool(path) and not path.startswith("/") and ".." not in path.split("/") and "\\" not in path


def lint_report(traces: Iterable[str]) -> LintReport:
    """Read the ruff errors and the files to reformat from the logs of the failed jobs."""
    report = LintReport()
    for trace in traces:
        cleaned = strip_ansi(trace)
        lines = error_summary(cleaned, 500)
        formats = [match.group("path") for line in cleaned.splitlines() if (match := RUFF_FORMAT.match(line.strip()))]
        report.format_paths.update(path for path in formats if safe_path(path))
        if not lines and not formats:
            # A failed job without any error line ruff knows: something else failed
            report.only_ruff = False
        for line in lines:
            match = RUFF_ERROR.match(line)
            if match and safe_path(match.group("path")):
                report.codes.add(match.group("code"))
                report.check_paths.add(match.group("path"))
            elif not RUFF_SUMMARY.match(line):
                report.only_ruff = False
    return report


def run_ruff(arguments: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "ruff", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=RUFF_TIMEOUT_S,
        check=False,
    )


def ruff_autofix(read: Callable[[str], str], report: LintReport) -> AutofixResult:
    """Run ruff on copies of the files the log names; return their new content and what remains.

    `read(path)` returns a file of the analysed commit. Only the rules reported in the log
    are fixed (--select), with ruff's safe fixes only, and the project's configuration.
    Any failure (ruff missing, a file unreadable, a timeout) leaves the files unchanged.
    """
    result = AutofixResult(report=report)
    if report.empty():
        return result
    paths = sorted(report.check_paths | report.format_paths)
    with tempfile.TemporaryDirectory(prefix="pipefix-ruff-") as folder:
        originals: dict[str, str] = {}
        for name in CONFIG_FILES:
            try:
                (Path(folder) / name).write_text(read(name), encoding="utf-8")
            # The project may have none of them: ruff then uses its defaults
            except Exception:
                continue
        for path in paths:
            try:
                originals[path] = read(path)
            except Exception:
                continue
            target = Path(folder) / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(originals[path], encoding="utf-8")
        check = sorted(path for path in report.check_paths if path in originals)
        fmt = sorted(path for path in report.format_paths if path in originals)
        try:
            if check and report.codes:
                select = ",".join(sorted(report.codes))
                run_ruff(["check", "--fix", "--exit-zero", "--quiet", "--select", select, *check], folder)
            if fmt:
                run_ruff(["format", "--quiet", *fmt], folder)
            if check and report.codes:
                left = run_ruff(["check", "--quiet", "--output-format=concise", "--select", select, *check], folder)
                result.remaining = [line for line in left.stdout.splitlines() if RUFF_ERROR.match(line.strip())]
            if fmt:
                left = run_ruff(["format", "--check", "--quiet", *fmt], folder)
                result.remaining += [
                    line for line in (left.stdout + left.stderr).splitlines() if RUFF_FORMAT.match(line.strip())
                ]
        except (OSError, subprocess.SubprocessError):
            return AutofixResult(report=report, remaining=["ruff could not run"])
        for path, before in originals.items():
            after = (Path(folder) / path).read_text(encoding="utf-8")
            if after != before:
                result.files[path] = after
    return result
