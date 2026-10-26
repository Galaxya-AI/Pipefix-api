"""Tool: a Python function the LLM can call (function calling)."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from llm.base import ToolDefinition
from tools.errors import ToolError
from tools.tags import check_tags


def object_schema(properties: dict[str, dict[str, Any]], required: Iterable[str] | None = None) -> dict[str, Any]:
    """JSON object schema of a tool's parameters; `required=None` means all of them."""
    required_names = list(properties) if required is None else list(required)
    return {"type": "object", "properties": properties, "required": required_names}


def result_to_text(result: Any) -> str:
    """The text itself, or indented JSON for any other value."""
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


@dataclass(frozen=True)
class Tool:
    """A tool the LLM can call; the description tells the LLM when and why to use it."""

    name: str
    description: str
    parameters: dict[str, Any]
    function: Callable[..., Any]
    tags: frozenset[str] = field(default_factory=frozenset)
    # True if the tool changes nothing outside the system
    read_only: bool = True

    def __post_init__(self) -> None:
        check_tags(self.tags)

    def definition(self) -> ToolDefinition:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}

    def missing_arguments(self, arguments: Mapping[str, Any]) -> list[str]:
        required = list(self.parameters.get("required", []))
        return [name for name in required if name not in arguments]

    def run(self, arguments: Mapping[str, Any]) -> str:
        """Run the tool; every failure is raised as a ToolError so the LLM can fix its call."""
        if "_invalid_json" in arguments:
            raise ToolError(f"Invalid JSON arguments: {arguments['_invalid_json']}")
        missing = self.missing_arguments(arguments)
        if missing:
            raise ToolError(f"Missing arguments: {', '.join(missing)}.")
        allowed = self.parameters.get("properties", {})
        # Arguments invented by the LLM are ignored
        clean = {key: value for key, value in arguments.items() if key in allowed}
        try:
            result = self.function(**clean)
        except ToolError:
            raise
        # Any other error goes back to the LLM instead of stopping the agent
        except Exception as error:
            raise ToolError(f"{type(error).__name__}: {error}") from error
        return result_to_text(result)


def bind_arguments(tool: Tool, values: Mapping[str, Any]) -> Tool:
    """Fix some arguments of a tool for a run and hide them from the LLM.

    E.g. the workflow fixes GitLab's "project" and "ref" so the LLM cannot read another project.
    """
    properties = tool.parameters.get("properties", {})
    fixed = {key: value for key, value in values.items() if key in properties}
    if not fixed:
        return tool
    parameters = copy.deepcopy(tool.parameters)
    for key in fixed:
        parameters["properties"].pop(key, None)
    parameters["required"] = [name for name in parameters.get("required", []) if name not in fixed]
    original = tool.function

    def bound(**arguments: Any) -> Any:
        # Fixed values win over the LLM's arguments
        return original(**{**arguments, **fixed})

    return replace(tool, parameters=parameters, function=bound)
