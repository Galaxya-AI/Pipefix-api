"""Agent configuration: the settings every agent must provide.

Each agent of the system is an instance of `AgentConfig`. The runtime reads
this configuration to build the LLM call: system prompt, allowed tools,
callable sub-agents, expected output and LLM override.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents.outputs import AgentOutput


class AgentConfig(BaseModel):
    """Configuration shared by all agents.

    Each agent of the system is an instance of this class. The runtime
    reads it to build every LLM call of the agent.

    Parameters
    ----------
    agent_name : str
        Unique name of the agent, used in logs, reports and routing.
    prompt_system : str
        Permanent instructions, sent as the system message.
    tool_tags : frozenset[str]
        Tags of the tools the agent receives.
    sub_agents : list[str]
        Names of the agents this agent can call as tools.
    output_model : type[AgentOutput]
        Model that validates the agent's final answer.
    llm_provider : str or None
        Provider override; None means the global setting.
    llm_model : str or None
        Model override; None means the global setting.

    Examples
    --------
    >>> DIAGNOSIS = AgentConfig(agent_name="diagnosis", prompt_system="...",
    ...                         tool_tags=frozenset({"code"}), output_model=DiagnosisOutput)
    >>> DIAGNOSIS.finish_tool_name
    'submit_diagnosis'
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_name: str = Field(
        ..., description="Agent name used as its unique key."
    )  # Unique name.
    prompt_system: str = Field(..., description="Permanent instructions for the LLM.")
    tool_tags: frozenset[str] = Field(
        ..., description="Tags of the tools the agent receives."
    )  # Allowed tools.
    sub_agents: list[str] = Field(  # Agents callable as tools.
        default_factory=list,
        description="Names of the agents this agent can call as tools (e.g. 'research'). Empty if none.",
    )
    output_model: type[AgentOutput] = Field(  # Output schema.
        ...,
        description="Expected structured output.",
    )  # End of field.
    llm_provider: str = Field(
        ...,
        description="LLM provider of this agent (must be a registered provider).",
    )
    llm_model: str = Field(
        ...,
        description="LLM model of this agent.",
    )

    @model_validator(mode="after")
    def agent_cannot_call_itself(self) -> AgentConfig:
        """Refuse an agent that lists itself as a sub-agent (infinite loop)."""
        if self.agent_name in self.sub_agents:  # Self-call…
            raise ValueError(
                f"Agent '{self.agent_name}' cannot call itself."
            )  # …refused.
        return self  # Valid configuration.

    @property
    def finish_tool_name(self) -> str:
        """Name of the tool the agent must call to finish (e.g. 'submit_diagnosis')."""
        return f"submit_{self.agent_name}"
