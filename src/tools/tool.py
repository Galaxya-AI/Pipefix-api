"""Tool: a Python function the LLM can call (function calling).

A Tool combines a name and a description (read by the LLM to decide when to
call it), a JSON schema of its parameters, the Python function that does the
work, and tags that say which agents receive it.
"""

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
    """Build the JSON object schema of the parameters of a tool.

    Parameters
    ----------
    properties : dict[str, dict[str, Any]]
        Description of each parameter (type, description...).
    required : Iterable[str] or None, default None
        Required parameters; None means all of them.

    Returns
    -------
    dict[str, Any]
        The JSON schema.

    Examples
    --------
    >>> object_schema({"path": {"type": "string"}})
    {'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': ['path']}
    """
    # Required parameters: all by default
    required_names: list[str] = list(properties) if required is None else list(required)
    # Object schema
    return {"type": "object", "properties": properties, "required": required_names}


def result_to_text(result: Any) -> str:
    """Convert the value returned by a tool function to text for the LLM.

    Parameters
    ----------
    result : Any
        Value returned by the function.

    Returns
    -------
    str
        The text itself, or indented JSON for any other value.
    """
    # Already text
    if isinstance(result, str):
        # Returned as-is
        return result
    # Any other value as indented JSON
    return json.dumps(result, ensure_ascii=False, indent=2, default=str)


@dataclass(frozen=True)
class Tool:
    """A tool the LLM can call.

    Parameters
    ----------
    name : str
        Unique tool name (letters, digits and "_").
    description : str
        Explanation for the LLM: when and why to use the tool.
    parameters : dict[str, Any]
        JSON schema of the arguments (see object_schema).
    function : Callable[..., Any]
        Python function run with the arguments as keywords.
    tags : frozenset[str], default frozenset()
        Destination tags; each one must be in tools.tags.KNOWN_TAGS.
    read_only : bool, default True
        True if the tool changes nothing outside the system.
    """

    # Unique name
    name: str
    # Description for the LLM
    description: str
    # JSON schema of the arguments
    parameters: dict[str, Any]
    # Function doing the work
    function: Callable[..., Any]
    # Destination tags
    tags: frozenset[str] = field(default_factory=frozenset)
    # Read-only by default
    read_only: bool = True

    def __post_init__(self) -> None:
        """Refuse unknown tags as soon as the tool is created.

        Raises
        ------
        ValueError
            If a tag is not in KNOWN_TAGS.
        """
        # Raises on an unknown tag
        check_tags(self.tags)

    def definition(self) -> ToolDefinition:
        """Return the tool definition sent to the LLM.

        Returns
        -------
        ToolDefinition
            A {name, description, parameters} dictionary.
        """
        # Definition in the internal format
        return {"name": self.name, "description": self.description, "parameters": self.parameters}

    def missing_arguments(self, arguments: Mapping[str, Any]) -> list[str]:
        """List the required arguments missing from a call.

        Parameters
        ----------
        arguments : Mapping[str, Any]
            Arguments given by the LLM.

        Returns
        -------
        list[str]
            Names of the missing arguments.
        """
        # Required arguments of the schema
        required: list[str] = list(self.parameters.get("required", []))
        # The ones not given
        return [name for name in required if name not in arguments]

    def run(self, arguments: Mapping[str, Any]) -> str:
        """Run the tool and return its result as text.

        Parameters
        ----------
        arguments : Mapping[str, Any]
            Arguments given by the LLM.

        Returns
        -------
        str
            Result of the tool as text.

        Raises
        ------
        ToolError
            If arguments are missing or invalid, or if the function fails.
        """
        # Arguments that were not valid JSON
        if "_invalid_json" in arguments:
            # The LLM must send its arguments again
            raise ToolError(f"Invalid JSON arguments: {arguments['_invalid_json']}")
        # Required arguments not given
        missing: list[str] = self.missing_arguments(arguments)
        # Some are missing
        if missing:
            # The LLM can fix its call
            raise ToolError(f"Missing arguments: {', '.join(missing)}.")
        # Declared parameters
        allowed: Mapping[str, Any] = self.parameters.get("properties", {})
        # Arguments invented by the LLM are ignored
        clean: dict[str, Any] = {key: value for key, value in arguments.items() if key in allowed}
        # Run the function
        try:
            # Raw result
            result: Any = self.function(**clean)
        # Expected error: kept as-is
        except ToolError:
            # Raised again unchanged
            raise
        # Any other error goes back to the LLM instead of stopping the agent
        except Exception as error:
            # Generic tool error
            raise ToolError(f"{type(error).__name__}: {error}") from error
        # Result as text
        return result_to_text(result)


def bind_arguments(tool: Tool, values: Mapping[str, Any]) -> Tool:
    """Fix some arguments of a tool for a run, and hide them from the LLM.

    Example: GitLab tools ask for "project" and "ref"; the workflow fixes
    them once for the run, so the LLM cannot read another project.

    Parameters
    ----------
    tool : Tool
        Original tool.
    values : Mapping[str, Any]
        Known values, e.g. {"project": "group/app", "ref": "a1b2c3"}.

    Returns
    -------
    Tool
        A new tool without these parameters, or the original tool if none applies.
    """
    # Parameters of the tool
    properties: Mapping[str, Any] = tool.parameters.get("properties", {})
    # Values that match a parameter
    fixed: dict[str, Any] = {key: value for key, value in values.items() if key in properties}
    # Nothing to fix
    if not fixed:
        # Tool unchanged
        return tool
    # Copy of the schema, so the original is not changed
    parameters: dict[str, Any] = copy.deepcopy(tool.parameters)
    # Current parameter name
    key: str
    # Remove every fixed parameter from the schema
    for key in fixed:
        # Removed from the properties
        parameters["properties"].pop(key, None)
    # Removed from the required list
    parameters["required"] = [name for name in parameters.get("required", []) if name not in fixed]
    # Function of the original tool
    original: Callable[..., Any] = tool.function

    def bound(**arguments: Any) -> Any:
        """Call the original function with the fixed values added.

        Parameters
        ----------
        **arguments : Any
            Arguments given by the LLM.

        Returns
        -------
        Any
            Result of the original function.
        """
        # Fixed values win over the LLM's arguments
        return original(**{**arguments, **fixed})

    # New tool with the reduced schema
    return replace(tool, parameters=parameters, function=bound)
