"""Exceptions raised while running agents."""

from __future__ import annotations


class AgentRunError(RuntimeError):
    """The agent could not produce a valid output within its limits.

    E.g. the LLM provider keeps failing, the turn limit is reached, or too many final answers are invalid.
    """
