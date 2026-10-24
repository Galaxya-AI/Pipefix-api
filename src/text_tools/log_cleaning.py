"""Text helpers for CI logs and tool results.

CI logs contain colour codes, section markers and sometimes secrets. These
functions clean them, mask secrets before anything is sent to an LLM and cut
overly long texts to keep the cost under control.
"""

from __future__ import annotations

import re

# ANSI colour and control codes
ANSI_PATTERN: re.Pattern[str] = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

# GitLab collapsible section markers
SECTION_PATTERN: re.Pattern[str] = re.compile(r"section_(?:start|end):\d+:[^\r\n]*?\r")

# Secret patterns and their replacement
SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"glpat-[A-Za-z0-9_\-]{10,}"), "glpat-***"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), "sk-***"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AKIA***"),
    (re.compile(r"\bglsa_[A-Za-z0-9_]{10,}"), "glsa_***"),
    (
        re.compile(
            r"(?i)((?:password|passwd|pwd|secret|token|api[_-]?key)\s*[:=]\s*)(\S+)"
        ),
        r"\1***",
    ),
    (re.compile(r"(?i)(https?://[^:/\s]+:)[^@\s]+@"), r"\1***@"),
)


def strip_ansi(text: str) -> str:
    """Remove colour codes and section markers from a CI log.

    Parameters
    ----------
    text : str
        Raw log.

    Returns
    -------
    str
        Readable log, with normalised line endings.
    """
    # Remove section markers
    cleaned: str = SECTION_PATTERN.sub("", text)
    # Remove colour codes
    cleaned = ANSI_PATTERN.sub("", cleaned)
    # Normalise line endings
    return cleaned.replace("\r\n", "\n").replace("\r", "\n")


def mask_secrets(text: str) -> str:
    """Mask known secrets in a text.

    Parameters
    ----------
    text : str
        Text that may contain secrets.

    Returns
    -------
    str
        The text with secrets replaced by "***".
    """
    # Working copy
    result: str = text
    # Current pattern
    pattern: re.Pattern[str]
    # Current replacement
    replacement: str
    # Apply every pattern
    for pattern, replacement in SECRET_PATTERNS:
        # Replace the matches
        result = pattern.sub(replacement, result)
    # Masked text
    return result


def keep_tail(text: str, max_lines: int) -> str:
    """Keep the last lines of a text (the end of a log holds the error).

    Parameters
    ----------
    text : str
        Full text.
    max_lines : int
        Number of lines to keep; 0 or less keeps everything.

    Returns
    -------
    str
        The last lines.
    """
    # No limit requested
    if max_lines <= 0:
        # Everything is kept
        return text
    # Lines of the text
    lines: list[str] = text.splitlines()
    # Last lines joined back
    return "\n".join(lines[-max_lines:])


def truncate(text: str, max_chars: int) -> str:
    """Cut an overly long text, keeping its beginning and its end.

    Parameters
    ----------
    text : str
        Text to cut.
    max_chars : int
        Maximum size in characters; 0 or less keeps everything.

    Returns
    -------
    str
        The text, possibly cut in the middle with an explicit marker.
    """
    # No limit, or short enough
    if max_chars <= 0 or len(text) <= max_chars:
        # Nothing to cut
        return text
    # One third for the beginning
    head_size: int = max_chars // 3
    # Two thirds for the end, often the most useful part
    tail_size: int = max_chars - head_size
    # Number of removed characters
    removed: int = len(text) - max_chars
    # Beginning, marker and end
    return (
        f"{text[:head_size]}\n[... {removed} characters cut ...]\n{text[-tail_size:]}"
    )


def clean_log(text: str, max_lines: int = 300) -> str:
    """Prepare a CI log for an LLM: cleaning, end of the log, masked secrets.

    Parameters
    ----------
    text : str
        Raw log.
    max_lines : int, default 300
        Number of final lines to keep.

    Returns
    -------
    str
        Log ready to be read by an agent.
    """
    # Clean, keep the end, then mask secrets
    return mask_secrets(keep_tail(strip_ansi(text), max_lines))
