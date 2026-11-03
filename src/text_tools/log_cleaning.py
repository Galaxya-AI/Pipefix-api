"""Clean CI logs and tool results, mask secrets and cut long texts before they reach an LLM."""

from __future__ import annotations

import re

ANSI_PATTERN = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

# GitLab collapsible section markers
SECTION_PATTERN = re.compile(r"section_(?:start|end):\d+:[^\r\n]*?\r")

# Prefix of each line when the GitLab runner adds timestamps, e.g. "2026-10-30T21:45:12.123456Z 00O "
# (stream 00, O for stdout or E for stderr, "+" for a continued line).
TIMESTAMP_PREFIX_PATTERN = re.compile(r"(?m)^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z \d{2}[OE]\+? ?")

SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"glpat-[A-Za-z0-9_\-]{10,}"), "glpat-***"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), "sk-***"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AKIA***"),
    (re.compile(r"\bglsa_[A-Za-z0-9_]{10,}"), "glsa_***"),
    (re.compile(r"(?i)((?:password|passwd|pwd|secret|token|api[_-]?key)\s*[:=]\s*)(\S+)"), r"\1***"),
    (re.compile(r"(?i)(https?://[^:/\s]+:)[^@\s]+@"), r"\1***@"),
)


def strip_ansi(text: str) -> str:
    """Remove colour codes and section markers, and normalise line endings."""
    cleaned = SECTION_PATTERN.sub("", text)
    cleaned = ANSI_PATTERN.sub("", cleaned)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    return TIMESTAMP_PREFIX_PATTERN.sub("", cleaned)


def mask_secrets(text: str) -> str:
    result = text
    for pattern, replacement in SECRET_PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def keep_tail(text: str, max_lines: int) -> str:
    """Keep the last max_lines lines (the end of a log holds the error); 0 or less keeps everything."""
    if max_lines <= 0:
        return text
    lines = text.splitlines()
    return "\n".join(lines[-max_lines:])


def truncate(text: str, max_chars: int) -> str:
    """Cut the middle of a text longer than max_chars, with a marker; 0 or less keeps everything."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    # The end gets two thirds: it is often the most useful part.
    head_size = max_chars // 3
    tail_size = max_chars - head_size
    removed = len(text) - max_chars
    return f"{text[:head_size]}\n[... {removed} characters cut ...]\n{text[-tail_size:]}"


# Lines that name an error, wherever they are in the log: pytest summary and collection
# errors, Python exceptions, ruff/flake8 (path:line:col: F401 ...) and mypy (path:line: error:).
ERROR_LINE_PATTERN = re.compile(
    r"^(?:FAILED |ERROR |E\s{2,}\S"
    r"|\S+\.py:\d+:(?:\d+:)? (?:[A-Z]{1,3}\d{2,4}\b|error:)"
    r"|(?:\w+\.)*\w*(?:Error|Exception): "
    r"|Interrupted: |Found \d+ errors?)"
)

# Parts that change from one run to the next for the same error: durations, hex ids, timestamps
VOLATILE_PATTERN = re.compile(r"\bin \d+(?:\.\d+)?s\b|0x[0-9a-f]+|\b\d{4}-\d{2}-\d{2}[T ][\d:.]+\S*")

SUMMARY_MAX_LINES = 80


# Lines that name the failed test or file (pytest short summary): kept first, so that the
# assertion details ("E  ..." lines, often many per test) cannot push them out of the list.
NAMING_LINE_PATTERN = re.compile(r"^(?:FAILED|ERROR) \S")


def error_summary(text: str, max_lines: int = SUMMARY_MAX_LINES) -> list[str]:
    """Return every distinct error line of a cleaned log (at most max_lines).

    The tail of a log shows the last error only; this list shows all of them,
    e.g. the 50 failed tests of a pytest run or every unused import found by ruff.
    The lines that name a failed test ("FAILED tests/x.py::test_y - ...") come first,
    then the other error lines, each group in the order of the log.
    """
    found: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if ERROR_LINE_PATTERN.match(stripped) and stripped not in found:
            found.append(stripped)
    naming = [line for line in found if NAMING_LINE_PATTERN.match(line)]
    others = [line for line in found if not NAMING_LINE_PATTERN.match(line)]
    return [*naming, *others][: max(0, max_lines)]


def failure_fingerprint(text: str) -> str:
    """Return the error lines of a raw log without durations or ids, to recognise a repeated failure."""
    lines = error_summary(strip_ansi(text), max_lines=200)
    if not lines:
        # No known error line: fall back on the end of the log
        lines = keep_tail(strip_ansi(text), 20).splitlines()
    return "\n".join(VOLATILE_PATTERN.sub("", line) for line in lines)


def clean_log(text: str, max_lines: int = 300) -> str:
    """Prepare a CI log for an LLM: strip codes, list all the errors, keep the end, mask secrets."""
    cleaned = strip_ansi(text)
    tail = keep_tail(cleaned, max_lines)
    tail_lines = {line.strip() for line in tail.splitlines()}
    # Errors already visible in the tail are not repeated
    hidden = [line for line in error_summary(cleaned) if line not in tail_lines]
    if hidden:
        header = "Errors found earlier in the log (the end of the log follows):"
        tail = "\n".join([header, *hidden, "", "--- end of the log ---", tail])
    return mask_secrets(tail)
