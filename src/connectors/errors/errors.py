"""Errors raised by the connectors."""

from __future__ import annotations


class ConnectorError(RuntimeError):
    """Communication error with an external tool.

    Parameters
    ----------
    message : str
        Description of the error.
    status_code : int or None, default None
        HTTP status of the answer, if there was one.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        # Message of the exception
        super().__init__(message)
        # HTTP status, used e.g. to detect a 404
        self.status_code: int | None = status_code
