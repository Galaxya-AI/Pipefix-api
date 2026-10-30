"""HTTP helper of the provider adapters: one request, errors classified as temporary or final."""

from __future__ import annotations

from typing import Any

import requests

from llm.errors import LLMError, LLMTransientError

RETRY_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504, 529})
MAX_RETRY_AFTER_S = 30.0


def read_retry_after(response: requests.Response) -> float | None:
    """Return the "retry-after" wait in seconds, capped, or None if missing or unreadable."""
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return min(float(raw), MAX_RETRY_AFTER_S)
    # The header may also be an HTTP date: ignored
    except ValueError:
        return None


def post_json(
    session: requests.Session,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_s: int = 180,
) -> dict[str, Any]:
    """POST a JSON body and return the decoded JSON object.

    Raise LLMTransientError on a network or temporary HTTP error, LLMError otherwise.
    """
    try:
        response = session.post(url, headers=headers, json=payload, timeout=timeout_s)
    except requests.exceptions.InvalidHeader:
        # The message of this error holds the header value, that is the API key: never logged.
        raise LLMError(
            f"Invalid request header towards {url}: the API key contains a space, a line break "
            "or another forbidden character. Check the value of the key (e.g. OPENAI_API_KEY)."
        ) from None
    except requests.RequestException as error:
        raise LLMTransientError(f"Network error towards {url}: {type(error).__name__}: {error}") from error
    if response.status_code in RETRY_STATUSES:
        raise LLMTransientError(
            f"Temporary LLM API error {response.status_code}: {response.text[:300]}",
            retry_after=read_retry_after(response),
        )
    if response.status_code >= 400:
        raise LLMError(f"LLM API error {response.status_code}: {response.text[:600]}")
    try:
        data = response.json()
    except ValueError as error:
        raise LLMError(f"Non-JSON answer from the LLM API: {response.text[:300]}") from error
    if not isinstance(data, dict):
        raise LLMError(f"Unexpected answer from the LLM API: {str(data)[:300]}")
    return data
