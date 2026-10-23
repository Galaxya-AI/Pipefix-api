"""Errors of the LLM layer.

Two kinds of errors, so that only the useful ones are retried:
- LLMError: final error, a new attempt will not fix it (invalid key, bad request, unreadable answer);
- LLMTransientError: temporary error, a new attempt may succeed (network, rate limit, overload).
"""

from __future__ import annotations


class LLMError(RuntimeError):
    """LLM call error that a new attempt will not fix (invalid key, bad request, unreadable answer)."""


class LLMTransientError(LLMError):
    """Temporary LLM call error that may succeed on a new attempt (network, rate limit, overload).

    Parameters
    ----------
    message : str
        Description of the error.
    retry_after : float or None, default None
        Wait advised by the API before the next attempt, in seconds, if any.
    """

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        # Normal exception message
        super().__init__(message)
        # Wait advised by the API (None = no advice)
        self.retry_after: float | None = retry_after
