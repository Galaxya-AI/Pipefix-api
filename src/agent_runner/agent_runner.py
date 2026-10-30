"""Agent runner: runs one agent until it returns a valid structured output.

The LLM answers with tool calls; the runner runs each tool (or sub-agent, "call_<name>") and sends the results
back. The run ends when the LLM calls the finish tool ("submit_<name>") with arguments the agent's output model
accepts; invalid arguments are sent back to the LLM so it can correct them.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from agent_base.agent_base import AgentConfig
from agent_errors.errors import AgentRunError
from agent_runner.runner_limits import RunnerLimits
from agents.outputs import AgentOutput
from journal.run_journal import RunJournal
from llm.base import LLMClient, LLMResponse, Message, ToolCall, ToolDefinition
from llm.content_messages import finish_reminder, system_message, tool_result_message, user_message
from llm.errors import LLMError
from llm.usage import UsageTracker
from text_tools.log_cleaning import truncate
from tools.agent_tools import agent_tool_definitions, sub_agent_from_tool_name
from tools.errors import ToolError
from tools.registry import ToolRegistry


class AgentRunner:
    def __init__(
        self,
        llm: LLMClient,
        tools: ToolRegistry,
        agents: dict[str, AgentConfig],
        limits: RunnerLimits | None = None,
        journal: RunJournal | None = None,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.agents = agents
        self.limits = limits or RunnerLimits()
        self.journal = journal
        self.usage = UsageTracker()

    def log(self, actor: str, event: str, data: dict[str, Any]) -> None:
        if self.journal is not None:
            self.journal.log(actor, event, data)

    def agent(self, agent_name: str) -> AgentConfig:
        config = self.agents.get(agent_name)
        if config is None:
            raise AgentRunError(f"Unknown agent: {agent_name}")
        return config

    def run(self, agent_name: str, task: str, depth: int = 0) -> AgentOutput:
        """Run an agent on a task until it returns a valid output (depth 0 for an agent run by the workflow).

        Raises AgentRunError if the LLM fails, or the agent reaches its turn or invalid-answer limit.
        """
        agent = self.agent(agent_name)
        definitions = agent_tool_definitions(agent, self.tools)
        messages: list[Message] = [system_message(agent.prompt_system), user_message(task)]
        invalid_answers = 0
        self.log(agent_name, "start", {"depth": depth, "tools": [item["name"] for item in definitions]})
        for turn in range(1, self.limits.max_turns + 1):
            response = self.call_llm(agent, messages, definitions)
            messages.append(response.as_message())
            if not response.tool_calls:
                messages.append(finish_reminder(agent.finish_tool_name))
                continue
            output: AgentOutput | None = None
            # Answer every call: provider APIs require one result per call
            for call in response.tool_calls:
                if call.name == agent.finish_tool_name:
                    output = self.validate_output(agent, call, messages)
                    if output is None:
                        invalid_answers += 1
                else:
                    messages.append(tool_result_message(call.id, call.name, self.run_tool(agent, call, depth)))
            if output is not None:
                self.log(agent_name, "finish", {"turns": turn, "output": output.model_dump()})
                return output
            if invalid_answers > self.limits.max_invalid_answers:
                raise AgentRunError(f"Agent '{agent_name}' gave {invalid_answers} invalid final answers.")
        raise AgentRunError(f"Agent '{agent_name}' reached its turn limit ({self.limits.max_turns}).")

    def call_llm(self, agent: AgentConfig, messages: list[Message], definitions: list[ToolDefinition]) -> LLMResponse:
        """Call the agent's LLM and count the usage; raises AgentRunError at the run's call limit or on failure."""
        # Cost guard for the whole run
        if self.usage.total_calls() >= self.limits.max_llm_calls:
            raise AgentRunError(f"LLM call limit of the run reached ({self.limits.max_llm_calls}).")
        try:
            response = self.llm.chat(agent.llm_provider, agent.llm_model, messages, definitions)
        except LLMError as error:
            self.log(
                agent.agent_name,
                "llm_error",
                {
                    "provider": agent.llm_provider,
                    "model": agent.llm_model,
                    "type": type(error).__name__,
                    "error": str(error),
                },
            )
            raise AgentRunError(f"LLM call failed for agent '{agent.agent_name}': {error}") from error
        self.usage.add(agent.agent_name, response)
        if response.content:
            self.log(agent.agent_name, "thought", {"text": truncate(response.content, 500)})
        return response

    def validate_output(self, agent: AgentConfig, call: ToolCall, messages: list[Message]) -> AgentOutput | None:
        """Validate the finish tool's arguments and append the tool result to `messages`.

        Returns None if the arguments are invalid; the validation errors are then sent back to the LLM.
        """
        try:
            output = agent.output_model.model_validate(call.arguments)
        except ValidationError as error:
            details = json.dumps(error.errors(include_url=False), ensure_ascii=False, default=str)
            feedback = f"Invalid result, fix it and call again: {details}"
            messages.append(tool_result_message(call.id, call.name, feedback))
            self.log(agent.agent_name, "invalid_output", {"errors": details})
            return None
        messages.append(tool_result_message(call.id, call.name, "Result recorded."))
        return output

    def run_tool(self, agent: AgentConfig, call: ToolCall, depth: int) -> str:
        """Run a tool or a sub-agent; errors are returned as text so the LLM can react."""
        sub_agent = sub_agent_from_tool_name(call.name)
        if sub_agent is not None and sub_agent in agent.sub_agents:
            return self.run_sub_agent(agent, sub_agent, call, depth)
        try:
            result = self.tools.execute_for(agent.tool_tags, call.name, call.arguments)
        except ToolError as error:
            result = f"Error: {error}"
        details = {"tool": call.name, "arguments": call.arguments, "preview": truncate(result, 300)}
        self.log(agent.agent_name, "tool_call", details)
        return result

    def run_sub_agent(self, agent: AgentConfig, sub_agent: str, call: ToolCall, depth: int) -> str:
        """Output of the sub-agent as JSON, or an error text: a failing sub-agent does not stop the caller."""
        if depth >= self.limits.max_sub_agent_depth:
            return f"Error: sub-agent depth limit reached ({self.limits.max_sub_agent_depth})."
        task = str(call.arguments.get("task", "")).strip()
        if not task:
            return "Error: the 'task' argument is required."
        self.log(agent.agent_name, "sub_agent_call", {"agent": sub_agent, "task": truncate(task, 300)})
        try:
            output = self.run(sub_agent, task, depth + 1)
        except AgentRunError as error:
            return f"Error: the {sub_agent} agent failed: {error}"
        return output.model_dump_json()
