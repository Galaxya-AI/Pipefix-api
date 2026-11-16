"""Cross-origin access for the web interface, which is deployed on its own and may live on another domain.

Only the visitor API (/web/...), /health and the API description are opened to browsers, and only
for the origins listed in CORS_ORIGINS. The server API (/api/v1/...) is called with API_TOKEN by
servers and scripts, never by a browser: it gets no CORS header.
"""

from __future__ import annotations

import os

from flask import Request, Response

# Headers the frontend sends: the JSON body, the key of a run, the visitor's GitLab token
ALLOWED_HEADERS = "Content-Type, X-Run-Key, X-Gitlab-Token"
ALLOWED_METHODS = "GET, POST, OPTIONS"
# How long a browser may keep a preflight answer, in seconds
MAX_AGE_S = "600"
OPEN_PATHS = ("/web/", "/health", "/api/openapi.yaml")


def allowed_origins(value: str | None = None) -> frozenset[str]:
    """Origins allowed to call the visitor API, from a comma-separated list such as
    "https://pipefix.example.com,http://localhost:5173". Empty: no cross-origin call (same domain only).

    A trailing slash is removed, because a browser never sends one in the Origin header.
    """
    raw = os.getenv("CORS_ORIGINS", "") if value is None else value
    return frozenset(origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip())


CORS_ORIGINS = allowed_origins()


def add_cors_headers(request: Request, response: Response, origins: frozenset[str] = CORS_ORIGINS) -> Response:
    """Allow the request's origin when it is listed and the path is open to browsers."""
    if not request.path.startswith(OPEN_PATHS):
        return response
    # The answer depends on the Origin header: a cache must never give one origin's answer to another
    response.headers.add("Vary", "Origin")
    origin = request.headers.get("Origin", "")
    if not origin or origin not in origins:
        return response
    response.headers["Access-Control-Allow-Origin"] = origin
    if request.method == "OPTIONS":
        response.headers["Access-Control-Allow-Headers"] = ALLOWED_HEADERS
        response.headers["Access-Control-Allow-Methods"] = ALLOWED_METHODS
        response.headers["Access-Control-Max-Age"] = MAX_AGE_S
    return response
