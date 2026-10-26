"""MCP client (stdio transport): starts an external MCP server and uses its tools.

Works with any MCP server that speaks stdio (Kubernetes, SonarQube...). The
server's tools become ordinary Tool objects: the agents make no difference
between a local tool and an MCP tool.
"""

from __future__ import annotations

import itertools
import json
import os
import queue
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from mcp_runtime.protocol import PROTOCOL_VERSION, SUPPORTED_VERSIONS, TAGS_META_KEY
from tools.errors import ToolError
from tools.tags import KNOWN_TAGS
from tools.tool import Tool

MCP_TIMEOUT_S = float(os.getenv("MCP_TIMEOUT_S", "60"))

# The only variables a server inherits: what a process needs to start, find its
# certificates and go through a proxy. Secrets reach a server only through its env.
INHERITED_VARIABLES = (
    "PATH", "HOME", "USER", "LANG", "LC_ALL", "TMPDIR", "TZ",
    "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
    "DOCKER_HOST",
)


def server_environment(env: Mapping[str, str] | None) -> dict[str, str]:
    inherited = {
        name: os.environ[name] for name in INHERITED_VARIABLES if name in os.environ
    }
    return {**inherited, **dict(env or {})}


class MCPError(RuntimeError):
    """Protocol or communication error with an MCP server."""


class MCPClient:
    """Connection to an MCP server started as a subprocess.

    The server gets INHERITED_VARIABLES plus env, never the rest of our environment.
    """

    def __init__(
        self,
        command: list[str],
        env: Mapping[str, str] | None = None,
        name: str = "",
        timeout_s: float = MCP_TIMEOUT_S,
    ) -> None:
        self.name = name or command[0]
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=server_environment(env),
        )
        self.ids = itertools.count(1)
        self.lock = threading.Lock()
        self.timeout_s = timeout_s
        # readline() cannot time out: a thread reads the output and request() waits
        # on this queue with a timeout. None marks the end of the output.
        self.lines: queue.Queue[str | None] = queue.Queue()
        threading.Thread(target=self.read_output, daemon=True).start()

    def read_output(self) -> None:
        if self.process.stdout is None:
            self.lines.put(None)
            return
        with self.process.stdout as output:
            for line in output:
                self.lines.put(line)
        self.lines.put(None)

    def send(self, message: dict[str, Any]) -> None:
        """Write one JSON-RPC message per line; raise MCPError if the server has stopped."""
        if self.process.poll() is not None or self.process.stdin is None:
            raise MCPError(f"The MCP server '{self.name}' has stopped.")
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def request(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Send a request and return the "result" field of its answer.

        Raise MCPError on a server error, if the channel closes, or if no answer
        comes within timeout_s (the server is then stopped).
        """
        with self.lock:
            request_id = next(self.ids)
            self.send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params or {},
                }
            )
            deadline = time.monotonic() + self.timeout_s
            while True:
                try:
                    line = self.lines.get(timeout=max(0.0, deadline - time.monotonic()))
                except queue.Empty:
                    self.process.kill()
                    raise MCPError(
                        f"The MCP server '{self.name}' did not answer '{method}' "
                        f"within {self.timeout_s:g} s."
                    ) from None
                if line is None:
                    raise MCPError(
                        f"The MCP server '{self.name}' stopped during '{method}'."
                    )
                try:
                    message = json.loads(line)
                # Stray non-JSON lines are ignored.
                except json.JSONDecodeError:
                    continue
                # Notifications and other answers are skipped.
                if message.get("id") != request_id:
                    continue
                if "error" in message:
                    error = message["error"]
                    raise MCPError(
                        f"{self.name}: {error.get('message')} (code {error.get('code')})"
                    )
                return dict(message.get("result") or {})

    def initialize(self) -> dict[str, Any]:
        """Run the MCP handshake and return the answer of the server."""
        result = self.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "ci-remediation", "version": "0.2.0"},
            },
        )
        version = result.get("protocolVersion")
        if version not in SUPPORTED_VERSIONS:
            raise MCPError(f"{self.name}: unsupported MCP protocol version {version!r}.")
        with self.lock:
            self.send(
                {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
            )
        return result

    def list_tools(self) -> list[dict[str, Any]]:
        """Return the MCP tool definitions of the server, following the pagination."""
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            page = self.request("tools/list", {"cursor": cursor} if cursor else {})
            tools.extend(page.get("tools") or [])
            cursor = page.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> str:
        """Call a tool and return its text result; raise ToolError if the server reports a tool error."""
        result = self.request(
            "tools/call", {"name": name, "arguments": dict(arguments)}
        )
        text = "\n".join(
            str(item.get("text", ""))
            for item in result.get("content") or []
            if item.get("type") == "text"
        )
        # Raised as ToolError so it is passed on to the LLM.
        if result.get("isError"):
            raise ToolError(text)
        return text

    def caller(self, name: str) -> Callable[..., str]:
        """Return a function that calls one remote tool with the tool arguments as keywords."""

        def call(**arguments: Any) -> str:
            return self.call_tool(name, arguments)

        return call

    def as_tools(
        self, default_tags: Iterable[str] = (), allowed: Iterable[str] | None = None
    ) -> list[Tool]:
        """Turn the tools of the server into Tool objects.

        default_tags go to tools that declare no known tag; allowed=None allows every tool.
        """
        allowed_names: frozenset[str] | None = (
            frozenset(allowed) if allowed is not None else None
        )
        defaults = frozenset(default_tags)
        tools: list[Tool] = []
        for definition in self.list_tools():
            name = str(definition.get("name", ""))
            if not name or (allowed_names is not None and name not in allowed_names):
                continue
            declared: frozenset[str] = frozenset(
                (definition.get("_meta") or {}).get(TAGS_META_KEY) or []
            )
            tags = (declared & KNOWN_TAGS) or defaults
            tools.append(
                Tool(
                    name,
                    str(definition.get("description", "")),
                    definition.get("inputSchema")
                    or {"type": "object", "properties": {}},
                    self.caller(name),
                    tags,
                    bool(
                        (definition.get("annotations") or {}).get("readOnlyHint", False)
                    ),
                )
            )
        return tools

    def close(self) -> None:
        """Stop the server: closing its input stops it, and it is killed after 5 seconds.

        The output is closed by the reading thread when the server exits.
        """
        try:
            if self.process.stdin and not self.process.stdin.closed:
                self.process.stdin.close()
            self.process.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            self.process.kill()
            self.process.wait(timeout=5)
