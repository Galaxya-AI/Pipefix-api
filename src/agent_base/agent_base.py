"""Agent configuration: the settings the runner needs to drive an agent."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agents.outputs import AgentOutput
from tools.tags import check_tags


class AgentConfig(BaseModel):
    """Configuration shared by all agents; each agent is an instance of a subclass."""

    # Defaults are validated too: subclasses set them from .env
    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)

    agent_name: str = Field(..., min_length=1, description="Agent name used as its unique key.")
    prompt_system: str = Field(..., min_length=1, description="Permanent instructions for the LLM.")
    tool_tags: frozenset[str] = Field(..., description="Tags of the tools the agent receives.")
    sub_agents: list[str] = Field(
        default_factory=list,
        description="Names of the agents this agent can call as tools (e.g. 'research'). Empty if none.",
    )
    output_model: type[AgentOutput] = Field(..., description="Expected structured output.")
    llm_provider: str = Field(..., min_length=1, description="LLM provider of this agent (from .env).")
    llm_model: str = Field(..., min_length=1, description="LLM model of this agent (from .env).")

    @field_validator("tool_tags")
    @classmethod
    def tags_must_be_known(cls, tags: frozenset[str]) -> frozenset[str]:
        return check_tags(tags)

    @field_validator("agent_name", "prompt_system", "llm_provider", "llm_model")
    @classmethod
    def text_must_not_be_empty(cls, value: str) -> str:
        """Strip the value; an empty one is usually a variable missing from .env."""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be empty (check the <AGENT>_LLM_PROVIDER and <AGENT>_LLM_MODEL values in .env)")
        return cleaned

    @model_validator(mode="after")
    def agent_cannot_call_itself(self) -> AgentConfig:
        if self.agent_name in self.sub_agents:
            raise ValueError(f"Agent '{self.agent_name}' cannot call itself.")
        return self

    @property
    def finish_tool_name(self) -> str:
        """Name of the tool the agent must call to finish (e.g. 'submit_diagnosis')."""
        return f"submit_{self.agent_name}"
