"""Agent-specific tools: the finish tool ("submit_<agent>") and the sub-agent tools ("call_<agent>")."""

from __future__ import annotations

from agent_base.agent_base import AgentConfig
from llm.base import ToolDefinition
from tools.registry import ToolRegistry
from tools.tool import object_schema

SUB_AGENT_PREFIX = "call_"


def sub_agent_tool_name(agent_name: str) -> str:
    return f"{SUB_AGENT_PREFIX}{agent_name}"


def sub_agent_from_tool_name(tool_name: str) -> str | None:
    """Name of the sub-agent behind a tool name, or None for any other tool."""
    if not tool_name.startswith(SUB_AGENT_PREFIX):
        return None
    return tool_name[len(SUB_AGENT_PREFIX) :]


def sub_agent_tool_definition(agent_name: str) -> ToolDefinition:
    return {
        "name": sub_agent_tool_name(agent_name),
        "description": f"Ask the {agent_name} agent to do a task. Returns its structured result as JSON.",
        "parameters": object_schema(
            {"task": {"type": "string", "description": "Precise task or question, with all useful context."}}
        ),
    }


def finish_tool_definition(agent: AgentConfig) -> ToolDefinition:
    """Its parameters are the JSON schema of the agent's output model."""
    return {
        "name": agent.finish_tool_name,
        "description": "Record your final result and finish. Call it once, when your work is done.",
        "parameters": agent.output_model.tool_schema(),
    }


def agent_tool_definitions(agent: AgentConfig, registry: ToolRegistry) -> list[ToolDefinition]:
    """Every tool an agent can see: its tagged tools, its sub-agents, then its finish tool."""
    definitions = registry.definitions(agent.tool_tags)
    definitions.extend(sub_agent_tool_definition(name) for name in agent.sub_agents)
    definitions.append(finish_tool_definition(agent))
    return definitions
