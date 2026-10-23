"""Exceptions raised by tools."""

from __future__ import annotations


class ToolError(Exception):
    """Expected tool error, sent back as-is to the LLM so it can correct its call.

    Examples: unknown tool, missing argument, file not found, edit text not found.
    """
