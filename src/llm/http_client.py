"""HTTP helper used by the provider adapters to call the LLM APIs.

It sends one request and classifies the errors: temporary errors become
LLMTransientError (retried by RetryingLLMClient), final errors become
LLMError (never retried).
"""

from __future__ import annotations

from typing import Any

import requests

from llm.base import LLMError, LLMTransientError

# HTTP codes of temporary errors (timeout, conflict, rate limit, server overload)
RETRY_STATUSES: frozenset[int] = frozenset({408, 409, 429, 500, 502, 503, 504, 529})

# Longest wait accepted from a "retry-after" header, in seconds
MAX_RETRY_AFTER_S: float = 30.0


def _retry_after(response: requests.Response) -> float | None:
    """Return the wait advised by the API in its "retry-after" header, capped, or None.

    Parameters
    ----------
    response : requests.Response
        Answer of the API.

    Returns
    -------
    float or None
        Wait in seconds, or None if the header is missing or unreadable.
    """
    # Raw value of the header
    raw: str | None = response.headers.get("retry-after")
    # No advice from the API
    if raw is None:
        # No wait advised
        return None
    # Convert the value to seconds
    try:
        # Advised wait, capped
        return min(float(raw), MAX_RETRY_AFTER_S)
    # Unreadable value (e.g. a date): ignore it
    except ValueError:
        # No usable advice
        return None


def post_json(
    # Reused HTTP session (connections kept alive, hence faster)
    session: requests.Session,
    # API address
    url: str,
    # HTTP headers (authentication...)
    headers: dict[str, str],
    # JSON body
    payload: dict[str, Any],
    # Maximum duration of the request, in seconds
    timeout_s: int = 180,
) -> dict[str, Any]:
    """Send one JSON POST request and return the decoded answer.

    Parameters
    ----------
    session : requests.Session
        Reused HTTP session (connections kept alive, hence faster).
    url : str
        API address.
    headers : dict[str, str]
        HTTP headers (authentication...).
    payload : dict[str, Any]
        JSON body.
    timeout_s : int, default 180
        Maximum duration of the request, in seconds.

    Returns
    -------
    dict[str, Any]
        The decoded JSON answer.

    Raises
    ------
    LLMTransientError
        On a network error or a temporary HTTP error (retried by RetryingLLMClient).
    LLMError
        On a final HTTP error or an answer that is not JSON.
    """
    # Send the request
    try:
        # Answer of the API
        response: requests.Response = session.post(url, headers=headers, json=payload, timeout=timeout_s)
    # Network problem: temporary
    except requests.RequestException as error:
        # Temporary error, worth a new attempt
        raise LLMTransientError(f"Network error towards {url}: {error}") from error
    # Temporary HTTP error (rate limit, overload)
    if response.status_code in RETRY_STATUSES:
        # Temporary error, with the wait advised by the API
        raise LLMTransientError(
            f"Temporary LLM API error {response.status_code}: {response.text[:300]}",
            retry_after=_retry_after(response),
        )
    # Final HTTP error (invalid key, bad request...)
    if response.status_code >= 400:
        # Final error with a useful message
        raise LLMError(f"LLM API error {response.status_code}: {response.text[:600]}")
    # Decode the JSON answer
    try:
        # Decoded answer
        data: dict[str, Any] = response.json()
    # Unreadable answer: final error
    except ValueError as error:
        # Clear error
        raise LLMError(f"Non-JSON answer from the LLM API: {response.text[:300]}") from error
    # Success
    return data
