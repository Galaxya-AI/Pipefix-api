"""Clean CI logs and tool results, mask secrets and cut long texts before they reach an LLM."""

from __future__ import annotations

import re

ANSI_PATTERN = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

# GitLab collapsible section markers
SECTION_PATTERN = re.compile(r"section_(?:start|end):\d+:[^\r\n]*?\r")

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
    return cleaned.replace("\r\n", "\n").replace("\r", "\n")


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


def clean_log(text: str, max_lines: int = 300) -> str:
    """Prepare a CI log for an LLM: strip codes, keep the end, mask secrets."""
    return mask_secrets(keep_tail(strip_ansi(text), max_lines))
