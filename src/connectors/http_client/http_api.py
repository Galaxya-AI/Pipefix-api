"""Small HTTP client shared by the connectors.

Base URL, permanent headers, timeout and new attempts of reads after a
temporary error.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

import requests

from connectors.errors.errors import ConnectorError

# HTTP statuses worth a new attempt (rate limit, temporary unavailability)
RETRY_STATUSES: frozenset[int] = frozenset({429, 502, 503, 504})


class HttpApi:
    """Small reusable HTTP client: base URL, permanent headers, timeout and retries of reads.

    Parameters
    ----------
    base_url : str
        Base URL of the API.
    headers : Mapping[str, str] or None, default None
        Headers added to every request (authentication...).
    timeout_s : int, default 30
        Maximum duration of a request, in seconds.
    retries : int, default 2
        New attempts for reads (GET) that hit a temporary error.
    sleep : Callable[[float], None], default time.sleep
        Wait function, replaced by a no-op in tests.
    """

    def __init__(
        self,
        base_url: str,
        headers: Mapping[str, str] | None = None,
        timeout_s: int = 30,
        retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        # URL without trailing "/"
        self.base_url: str = base_url.rstrip("/")
        # Maximum duration of a request
        self.timeout_s: int = timeout_s
        # New attempts for reads
        self.retries: int = retries
        # Wait function
        self.sleep: Callable[[float], None] = sleep
        # Session: connections are reused, hence faster
        self.session: requests.Session = requests.Session()
        # Permanent headers
        self.session.headers.update(dict(headers or {}))

    def request(
        self, method: str, path: str, params: Mapping[str, Any] | None = None, json_body: Any = None
    ) -> requests.Response:
        """Send a request and check its status.

        Parameters
        ----------
        method : str
            HTTP method (GET, POST...).
        path : str
            Path relative to the base URL.
        params : Mapping[str, Any] or None, default None
            URL parameters.
        json_body : Any, default None
            JSON body.

        Returns
        -------
        requests.Response
            The HTTP answer.

        Raises
        ------
        ConnectorError
            On a network error or an HTTP status of 400 or more.
        """
        # Full URL
        url: str = f"{self.base_url}{path}"
        # Only reads are retried: a write must never be sent twice
        attempts: int = self.retries + 1 if method.upper() == "GET" else 1
        # Current attempt
        attempt: int
        # Attempt loop
        for attempt in range(1, attempts + 1):
            # Send the request
            try:
                # HTTP answer
                response: requests.Response = self.session.request(
                    method, url, params=params, json=json_body, timeout=self.timeout_s
                )
            # Network error
            except requests.RequestException as error:
                # Last attempt
                if attempt == attempts:
                    # Give up
                    raise ConnectorError(f"{method} {url}: network error ({error})") from error
                # Growing wait
                self.sleep(float(2**attempt))
                # New attempt
                continue
            # Temporary error and attempts left
            if response.status_code in RETRY_STATUSES and attempt < attempts:
                # Growing wait
                self.sleep(float(2**attempt))
                # New attempt
                continue
            # Final error
            if response.status_code >= 400:
                # Clear message with the status
                raise ConnectorError(
                    f"{method} {path} -> {response.status_code}: {response.text[:400]}", response.status_code
                )
            # Success
            return response
        # Only reached if attempts is 0, which cannot happen
        raise ConnectorError(f"{method} {url}: no attempt made")

    def get_json(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        """Read a JSON answer.

        Parameters
        ----------
        path : str
            Relative path.
        params : Mapping[str, Any] or None, default None
            URL parameters.

        Returns
        -------
        Any
            Decoded answer.
        """
        # Request then decode
        return self.request("GET", path, params=params).json()

    def get_text(self, path: str, params: Mapping[str, Any] | None = None) -> str:
        """Read a raw text answer.

        Parameters
        ----------
        path : str
            Relative path.
        params : Mapping[str, Any] or None, default None
            URL parameters.

        Returns
        -------
        str
            Text of the answer.
        """
        # HTTP answer
        response: requests.Response = self.request("GET", path, params=params)
        # UTF-8 when the server gives no encoding
        response.encoding = response.encoding or "utf-8"
        # Text
        return response.text

    def post_json(self, path: str, body: Any = None) -> Any:
        """Send a JSON body and read the JSON answer.

        Parameters
        ----------
        path : str
            Relative path.
        body : Any, default None
            JSON body.

        Returns
        -------
        Any
            Decoded answer, or None if it is empty.
        """
        # HTTP answer
        response: requests.Response = self.request("POST", path, json_body=body)
        # Decode only when there is content
        return response.json() if response.content else None
