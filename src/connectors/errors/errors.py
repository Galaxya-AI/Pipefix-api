"""Errors raised by the connectors."""

from __future__ import annotations


class ConnectorError(RuntimeError):
    """Communication error with an external tool; status_code is the HTTP status, if any."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
