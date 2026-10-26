"""Toolbox: gathers the tools of the connectors and of external MCP servers, and builds a run's registry.

The external MCP servers come from mcp_runtime.config. Only their read-only tools
are kept: the agents analyse and propose, the publication alone writes to GitLab.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterable

from changeset.changeset import Changeset
from connectors.base.base import Connector
from journal.run_journal import RunJournal
from mcp_runtime.client import MCPClient
from mcp_runtime.config import MCP_SERVERS_FILE, load_mcp_servers
from policy.data_policy import DataPolicy
from tools.registry import ToolRegistry
from tools.tags import TAG_CONTEXT, TAG_RESEARCH
from tools.tool import Tool, bind_arguments


class Toolbox:
    """Available tools, and the functions that release resources at the end (stop MCP servers...)."""

    def __init__(self, tools: list[Tool], closers: list[Callable[[], None]]) -> None:
        self.tools = tools
        self.closers = closers

    def close(self) -> None:
        """Release every resource, without stopping on an error."""
        for closer in self.closers:
            try:
                closer()
            # A failed release must never make the run fail
            except Exception as error:
                print(f"[toolbox] could not close a resource: {error}", file=sys.stderr)


def build_toolbox(connectors: Iterable[Connector], servers_file: str = MCP_SERVERS_FILE) -> Toolbox:
    tools = [tool for connector in connectors for tool in connector.tools()]
    # Created first, so the servers already started can be stopped if a later one fails
    toolbox = Toolbox(tools, [])
    try:
        for name, config in load_mcp_servers(servers_file).items():
            client = MCPClient(config["command"], env=config["env"], name=name)
            toolbox.closers.append(client.close)
            client.initialize()
            served = client.as_tools(config.get("tags") or [TAG_CONTEXT], config.get("tools"))
            tools.extend(tool for tool in served if tool.read_only)
            refused = sorted(tool.name for tool in served if not tool.read_only)
            if refused:
                # A tool that writes would let an agent bypass the publication (draft MR, dry run).
                print(
                    f"[toolbox] MCP server '{name}': tools not read-only, ignored: {', '.join(refused)}",
                    file=sys.stderr,
                )
    except Exception:
        toolbox.close()
        raise
    return toolbox


def build_run_registry(
    tools: Iterable[Tool],
    project: str,
    ref: str,
    changeset: Changeset,
    policy: DataPolicy,
    journal: RunJournal | None = None,
    allow_research: bool = True,
) -> ToolRegistry:
    """Prepare the tools of one run: project and commit fixed, data policy applied, edit tools added.

    allow_research=False removes the web research tools (confidential projects).
    """
    values = {"project": project, "ref": ref}
    every_tool = [*tools, *changeset.tools()]
    selected = [tool for tool in every_tool if allow_research or TAG_RESEARCH not in tool.tags]
    return ToolRegistry(policy.guard(bind_arguments(tool, values), journal) for tool in selected)
