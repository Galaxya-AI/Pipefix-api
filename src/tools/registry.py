"""Tool registry: every available tool, indexed by name.

The runner asks the registry for the tools of an agent (by its tags) and
runs a tool only if the agent is allowed to use it.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from typing import Any

from llm.base import ToolDefinition
from text_tools.log_cleaning import truncate
from tools.errors import ToolError
from tools.tool import Tool

# Maximum size of a tool result sent back to the LLM, in characters
TOOL_MAX_OUTPUT_CHARS: int = int(os.getenv("TOOL_MAX_OUTPUT_CHARS", "12000"))


class ToolRegistry:
    """Set of tools indexed by name.

    Parameters
    ----------
    tools : Iterable[Tool], default ()
        Tools registered right away.
    max_output_chars : int, default TOOL_MAX_OUTPUT_CHARS
        Maximum size of a tool result sent back to the LLM.

    Examples
    --------
    >>> registry = ToolRegistry([read_file_tool])
    >>> registry.execute_for(frozenset({"code"}), "read_file", {"path": "app.py"})
    """

    def __init__(self, tools: Iterable[Tool] = (), max_output_chars: int = TOOL_MAX_OUTPUT_CHARS) -> None:
        # Tools by name
        self.tools: dict[str, Tool] = {}
        # Result size limit
        self.max_output_chars: int = max_output_chars
        # Current tool
        tool: Tool
        # Register every given tool
        for tool in tools:
            # Registration
            self.add(tool)

    def add(self, tool: Tool) -> None:
        """Register a tool.

        Parameters
        ----------
        tool : Tool
            Tool to register.

        Raises
        ------
        ValueError
            If a tool with the same name is already registered.
        """
        # Two tools with the same name would hide each other
        if tool.name in self.tools:
            # Configuration error
            raise ValueError(f"Duplicate tool: {tool.name}")
        # Registration
        self.tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """Return a tool by name.

        Parameters
        ----------
        name : str
            Tool name.

        Returns
        -------
        Tool or None
            The tool, or None if it is not registered.
        """
        # Direct lookup
        return self.tools.get(name)

    def names(self) -> list[str]:
        """Return the names of the registered tools, sorted.

        Returns
        -------
        list[str]
            Sorted tool names.
        """
        # Alphabetical order
        return sorted(self.tools)

    def for_tags(self, tags: Iterable[str]) -> list[Tool]:
        """Return the tools carrying at least one of the given tags.

        Parameters
        ----------
        tags : Iterable[str]
            Tags of an agent.

        Returns
        -------
        list[Tool]
            Matching tools, sorted by name.
        """
        # Wanted tags
        wanted: frozenset[str] = frozenset(tags)
        # Tools sharing at least one tag
        return [self.tools[name] for name in self.names() if self.tools[name].tags & wanted]

    def definitions(self, tags: Iterable[str]) -> list[ToolDefinition]:
        """Return the definitions of the tools carrying at least one of the given tags.

        Parameters
        ----------
        tags : Iterable[str]
            Tags of an agent.

        Returns
        -------
        list[ToolDefinition]
            Definitions sent to the LLM.
        """
        # One definition per allowed tool
        return [tool.definition() for tool in self.for_tags(tags)]

    def execute_for(self, tags: Iterable[str], name: str, arguments: Mapping[str, Any]) -> str:
        """Run a tool on behalf of an agent, if its tags allow it.

        Parameters
        ----------
        tags : Iterable[str]
            Tags of the agent.
        name : str
            Requested tool.
        arguments : Mapping[str, Any]
            Arguments given by the LLM.

        Returns
        -------
        str
            Result of the tool, cut to max_output_chars.

        Raises
        ------
        ToolError
            If the tool is unknown, not allowed for these tags, or fails.
        """
        # Requested tool
        tool: Tool | None = self.get(name)
        # Unknown tool, or not allowed for this agent
        if tool is None or not tool.tags & frozenset(tags):
            # The LLM must choose another tool
            raise ToolError(f"Unknown or not allowed tool: {name}")
        # Run the tool and cut the result
        return truncate(tool.run(arguments), self.max_output_chars)
