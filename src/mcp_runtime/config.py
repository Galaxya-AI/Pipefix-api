"""Configuration of the external MCP servers: a JSON file, with ${VAR} taken from the environment.

The file is MCP_SERVERS_FILE (default config/mcp_servers.json). Without MCP_SERVERS_FILE,
a missing default file only means no server; a file named explicitly must exist. Format:
{"name": {"command": ["prog", "arg"], "env": {"TOKEN": "${GITLAB_TOKEN}"}, "tags": ["context"], "tools": ["tool1"]}}
"tags" are given to tools that declare none; "tools" limits the allowed tools.
${VAR} is replaced by the environment variable, so secrets stay in .env.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

DEFAULT_SERVERS_FILE = "config/mcp_servers.json"
MCP_SERVERS_FILE = os.getenv("MCP_SERVERS_FILE", "")

VARIABLE = re.compile(r"\$\{(\w+)\}")


def load_mcp_servers(path: str) -> dict[str, dict[str, Any]]:
    """Read the MCP servers file (empty path: the default one, optional). Raise ValueError if it is invalid."""
    file = Path(path or DEFAULT_SERVERS_FILE)
    if not file.exists():
        if path:
            raise ValueError(f"MCP_SERVERS_FILE: {path} does not exist.")
        print(f"[toolbox] no {DEFAULT_SERVERS_FILE}: no external MCP server.", file=sys.stderr)
        return {}
    try:
        servers = json.loads(file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{path} is not valid JSON: {error}") from error
    if not isinstance(servers, dict):
        raise ValueError(f"{path} must be a JSON object {{name: configuration}}.")
    for name, config in servers.items():
        check_server(name, config)
        config["command"] = [expand(arg, name) for arg in config["command"]]
        config["env"] = {key: expand(value, name) for key, value in config.get("env", {}).items()}
    return servers


def check_server(name: str, config: Any) -> None:
    def is_strings(value: Any) -> bool:
        return isinstance(value, list) and all(isinstance(item, str) for item in value)

    if not isinstance(config, dict):
        raise ValueError(f"MCP server '{name}': the configuration must be an object.")
    if not is_strings(config.get("command")) or not config["command"]:
        raise ValueError(f"MCP server '{name}': 'command' must be a non-empty list of strings.")
    env = config.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(value, str) for value in env.values()):
        raise ValueError(f"MCP server '{name}': 'env' must map names to strings.")
    for key in ("tags", "tools"):
        if key in config and not is_strings(config[key]):
            raise ValueError(f"MCP server '{name}': '{key}' must be a list of strings.")


def expand(value: str, server: str) -> str:
    def replace(match: re.Match[str]) -> str:
        variable = match.group(1)
        if variable not in os.environ:
            # Only the name is shown, never a value
            raise ValueError(f"MCP server '{server}': the environment variable {variable} is not set.")
        return os.environ[variable]

    return VARIABLE.sub(replace, value)
