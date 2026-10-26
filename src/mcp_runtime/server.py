"""MCP server (stdio transport) that exposes the connector tools to any MCP client.

Supported methods: initialize, ping, tools/list, tools/call, and the
notifications/initialized notification. Tool tags are sent in the "_meta"
field, and the read-only nature in the annotations (readOnlyHint).
On stdio, the standard output is reserved for the protocol: logs go to the
error output.
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from mcp_runtime.protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    PROTOCOL_VERSION,
    SUPPORTED_VERSIONS,
    TAGS_META_KEY,
    error_message,
    result_message,
)
from tools.errors import ToolError
from tools.tool import Tool


def describe_tool(tool: Tool) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "inputSchema": tool.parameters,
        "annotations": {"readOnlyHint": tool.read_only},
        "_meta": {TAGS_META_KEY: sorted(tool.tags)},
    }


class MCPServer:
    """Minimal MCP server serving a list of tools."""

    def __init__(self, name: str, version: str, tools: list[Tool]) -> None:
        self.name = name
        self.version = version
        self.tools = {tool.name: tool for tool in tools}

    def handle(self, message: Any) -> dict[str, Any] | None:
        """Process one decoded JSON-RPC message; return the answer, or None for a notification."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return error_message(None, INVALID_REQUEST, "JSON-RPC 2.0 message expected.")
        method = str(message.get("method", ""))
        # Missing for a notification
        request_id = message.get("id")
        params = message.get("params") or {}
        if request_id is None:
            return None
        if not isinstance(params, dict):
            return error_message(request_id, INVALID_PARAMS, "params must be an object.")
        # The server must never stop on an error.
        try:
            if method == "initialize":
                return result_message(request_id, self.initialize(params))
            if method == "ping":
                return result_message(request_id, {})
            if method == "tools/list":
                return result_message(request_id, {"tools": [describe_tool(tool) for tool in self.tools.values()]})
            if method == "tools/call":
                return self.call(request_id, params)
            return error_message(request_id, METHOD_NOT_FOUND, f"Unknown method: {method}")
        except Exception as error:
            # Details stay in the server logs, the client only learns that it failed.
            print(f"[mcp] '{method}' failed: {type(error).__name__}: {error}", file=sys.stderr)
            return error_message(request_id, INTERNAL_ERROR, "Internal error.")

    def initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested = str(params.get("protocolVersion", PROTOCOL_VERSION))
        # An unsupported version gets the default one.
        version = requested if requested in SUPPORTED_VERSIONS else PROTOCOL_VERSION
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": self.name, "version": self.version},
        }

    def call(self, request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        """Run a "tools/call" request ({"name": ..., "arguments": {...}})."""
        name = str(params.get("name", ""))
        tool = self.tools.get(name)
        if tool is None:
            return error_message(request_id, INVALID_PARAMS, f"Unknown tool: {name}")
        try:
            text = tool.run(params.get("arguments") or {})
            is_error = False
        # An expected tool error is reported to the client as a tool result, not a protocol error.
        except ToolError as error:
            text = str(error)
            is_error = True
        return result_message(request_id, {"content": [{"type": "text", "text": text}], "isError": is_error})

    def serve(self, stdin: TextIO = sys.stdin, stdout: TextIO = sys.stdout) -> None:
        """Read one request per line and write one answer per line, until the input closes."""
        # stdout is reserved for the protocol.
        print(f"[mcp] server '{self.name}' ready, {len(self.tools)} tools.", file=sys.stderr)
        for line in stdin:
            if not line.strip():
                continue
            try:
                answer: dict[str, Any] | None = self.handle(json.loads(line))
            except json.JSONDecodeError:
                answer = error_message(None, PARSE_ERROR, "Unreadable JSON.")
            if answer is not None:
                stdout.write(json.dumps(answer, ensure_ascii=False) + "\n")
                stdout.flush()
