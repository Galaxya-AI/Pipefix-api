"""Errors of the LLM layer, split so that only temporary errors are retried."""

from __future__ import annotations


class LLMError(RuntimeError):
    """LLM call error that a new attempt will not fix (invalid key, bad request, unreadable answer)."""


class LLMTransientError(LLMError):
    """Temporary LLM call error that may succeed on a new attempt (network, rate limit, overload).

    `retry_after` is the wait advised by the API, in seconds, if any.
    """

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after
