"""Tool registry: every available tool, indexed by name."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from typing import Any

from llm.base import ToolDefinition
from text_tools.log_cleaning import truncate
from tools.errors import ToolError
from tools.tool import Tool

# Maximum size, in characters, of a tool result sent back to the LLM
TOOL_MAX_OUTPUT_CHARS = int(os.getenv("TOOL_MAX_OUTPUT_CHARS", "12000"))


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] = (), max_output_chars: int = TOOL_MAX_OUTPUT_CHARS) -> None:
        self.tools: dict[str, Tool] = {}
        self.max_output_chars = max_output_chars
        for tool in tools:
            self.add(tool)

    def add(self, tool: Tool) -> None:
        # Two tools with the same name would hide each other
        if tool.name in self.tools:
            raise ValueError(f"Duplicate tool: {tool.name}")
        self.tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def names(self) -> list[str]:
        return sorted(self.tools)

    def for_tags(self, tags: Iterable[str]) -> list[Tool]:
        """Tools carrying at least one of the tags, sorted by name."""
        wanted = frozenset(tags)
        return [self.tools[name] for name in self.names() if self.tools[name].tags & wanted]

    def definitions(self, tags: Iterable[str]) -> list[ToolDefinition]:
        return [tool.definition() for tool in self.for_tags(tags)]

    def execute_for(self, tags: Iterable[str], name: str, arguments: Mapping[str, Any]) -> str:
        """Run a tool if the agent's tags allow it; the result is cut to max_output_chars.

        Raises ToolError if the tool is unknown, not allowed, or fails.
        """
        tool = self.get(name)
        if tool is None or not tool.tags & frozenset(tags):
            raise ToolError(f"Unknown or not allowed tool: {name}")
        return truncate(tool.run(arguments), self.max_output_chars)
