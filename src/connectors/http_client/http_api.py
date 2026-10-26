"""HTTP client shared by the connectors."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

import requests

from connectors.errors.errors import ConnectorError

RETRY_STATUSES = frozenset({429, 502, 503, 504})


class HttpApi:
    """Base URL, permanent headers, timeout, and retries of reads after a temporary error."""

    def __init__(
        self,
        base_url: str,
        headers: Mapping[str, str] | None = None,
        timeout_s: int = 30,
        retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.retries = retries
        self.sleep = sleep
        self.session = requests.Session()
        self.session.headers.update(dict(headers or {}))

    def request(
        self, method: str, path: str, params: Mapping[str, Any] | None = None, json_body: Any = None
    ) -> requests.Response:
        """Send a request; raise ConnectorError on a network error or an HTTP status >= 400."""
        url = f"{self.base_url}{path}"
        # Only reads are retried: a write must never be sent twice.
        attempts = self.retries + 1 if method.upper() == "GET" else 1
        for attempt in range(1, attempts + 1):
            try:
                response = self.session.request(method, url, params=params, json=json_body, timeout=self.timeout_s)
            except requests.RequestException as error:
                if attempt == attempts:
                    raise ConnectorError(f"{method} {url}: network error ({error})") from error
                self.sleep(float(2**attempt))
                continue
            if response.status_code in RETRY_STATUSES and attempt < attempts:
                self.sleep(float(2**attempt))
                continue
            if response.status_code >= 400:
                raise ConnectorError(
                    f"{method} {path} -> {response.status_code}: {response.text[:400]}", response.status_code
                )
            return response
        raise ConnectorError(f"{method} {url}: no attempt made")

    def get_json(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        return self.request("GET", path, params=params).json()

    def get_text(self, path: str, params: Mapping[str, Any] | None = None) -> str:
        response = self.request("GET", path, params=params)
        response.encoding = response.encoding or "utf-8"
        return response.text

    def post_json(self, path: str, body: Any = None) -> Any:
        response = self.request("POST", path, json_body=body)
        return response.json() if response.content else None
