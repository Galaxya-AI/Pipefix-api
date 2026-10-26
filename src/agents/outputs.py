"""Structured outputs returned by the agents.

Every agent ends by calling a finish tool whose arguments are validated against a subclass of AgentOutput.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentOutput(BaseModel):
    """Base class of every structured agent output."""

    # Unknown keys are refused so that an invented field fails validation and is reported to the LLM
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    summary: str = Field(..., min_length=1, description="Short conclusion of the agent.")

    @classmethod
    def tool_schema(cls) -> dict[str, Any]:
        """JSON schema used as the parameters of the finish tool."""
        return cls.model_json_schema()
