"""JSON-RPC 2.0 constants and helpers shared by the MCP server and client."""

from __future__ import annotations

from typing import Any

# Announced by default
PROTOCOL_VERSION = "2025-06-18"

# Accepted from a client
SUPPORTED_VERSIONS = frozenset({"2024-11-05", "2025-03-26", "2025-06-18"})

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
TAGS_META_KEY = "tags"


def result_message(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def error_message(request_id: Any, code: int, message: str) -> dict[str, Any]:
    """Build a JSON-RPC error answer; request_id is None when the request was unreadable."""
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }
