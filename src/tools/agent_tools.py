"""Tool definitions specific to agents: the finish tool and the sub-agent tools.

- The finish tool ("submit_<agent_name>") has the JSON schema of the agent's
  output model: calling it ends the agent with a structured result.
- A sub-agent tool ("call_<agent_name>") lets an agent ask another agent a
  question (e.g. the diagnosis agent calls the research agent).
"""

from __future__ import annotations

from agent_base.agent_base import AgentConfig
from llm.base import ToolDefinition
from tools.registry import ToolRegistry
from tools.tool import object_schema

# Prefix of the tools that call a sub-agent
SUB_AGENT_PREFIX: str = "call_"


def sub_agent_tool_name(agent_name: str) -> str:
    """Return the tool name used to call a sub-agent.

    Parameters
    ----------
    agent_name : str
        Name of the sub-agent.

    Returns
    -------
    str
        Tool name, e.g. "call_research".
    """
    # Prefix followed by the agent name
    return f"{SUB_AGENT_PREFIX}{agent_name}"


def sub_agent_from_tool_name(tool_name: str) -> str | None:
    """Return the sub-agent name behind a tool name, if it is a sub-agent tool.

    Parameters
    ----------
    tool_name : str
        Tool name requested by the LLM.

    Returns
    -------
    str or None
        Name of the sub-agent, or None for any other tool.
    """
    # Not a sub-agent tool
    if not tool_name.startswith(SUB_AGENT_PREFIX):
        # Ordinary tool
        return None
    # Name after the prefix
    return tool_name[len(SUB_AGENT_PREFIX) :]


def sub_agent_tool_definition(agent_name: str) -> ToolDefinition:
    """Return the definition of the tool that calls a sub-agent.

    Parameters
    ----------
    agent_name : str
        Name of the sub-agent.

    Returns
    -------
    ToolDefinition
        Definition with a single "task" argument.
    """
    # Tool with one text argument: the question or task for the sub-agent
    return {
        "name": sub_agent_tool_name(agent_name),
        "description": f"Ask the {agent_name} agent to do a task. Returns its structured result as JSON.",
        "parameters": object_schema(
            {"task": {"type": "string", "description": "Precise task or question, with all useful context."}}
        ),
    }


def finish_tool_definition(agent: AgentConfig) -> ToolDefinition:
    """Return the definition of the finish tool of an agent.

    Parameters
    ----------
    agent : AgentConfig
        Agent configuration.

    Returns
    -------
    ToolDefinition
        Definition whose parameters are the JSON schema of the output model.
    """
    # Tool whose arguments are the final structured result
    return {
        "name": agent.finish_tool_name,
        "description": "Record your final result and finish. Call it once, when your work is done.",
        "parameters": agent.output_model.tool_schema(),
    }


def agent_tool_definitions(agent: AgentConfig, registry: ToolRegistry) -> list[ToolDefinition]:
    """Return every tool an agent can see: its tagged tools, its sub-agents and its finish tool.

    Parameters
    ----------
    agent : AgentConfig
        Agent configuration.
    registry : ToolRegistry
        All available tools.

    Returns
    -------
    list[ToolDefinition]
        Definitions sent to the LLM.
    """
    # Tools matching the agent tags
    definitions: list[ToolDefinition] = registry.definitions(agent.tool_tags)
    # One tool per sub-agent
    definitions.extend(sub_agent_tool_definition(name) for name in agent.sub_agents)
    # Finish tool last
    definitions.append(finish_tool_definition(agent))
    # Complete list
    return definitions
