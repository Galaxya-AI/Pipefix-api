"""Types and interfaces shared by the whole LLM layer.

Internal conversation format, independent of any provider (each provider
adapter converts it to its own API format):
- {"role": "system", "content": str}
- {"role": "user", "content": str}
- {"role": "assistant", "content": str, "tool_calls": [{"id": str, "name": str, "arguments": dict}]}
- {"role": "tool", "tool_call_id": str, "name": str, "content": str}
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeAlias

Message: TypeAlias = dict[str, Any]
# Name, description and JSON schema of the parameters
ToolDefinition: TypeAlias = dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    """Tool call requested by the LLM; `id` is sent back with the tool result."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass(frozen=True)
class LLMResponse:
    """Normalised answer of the LLM, whatever the provider."""

    # May be empty when the LLM only calls tools
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    input_tokens: int = 0
    output_tokens: int = 0

    def as_message(self) -> Message:
        return {
            "role": "assistant",
            "content": self.content,
            "tool_calls": [call.as_dict() for call in self.tool_calls],
        }


class LLMProvider(ABC):
    """Adapter of one provider: converts the internal format, calls the API, returns an LLMResponse.

    `name` is the provider name used in .env (e.g. "openai", "ollama").
    """

    def __init__(self, name: str) -> None:
        self.name = name

    @abstractmethod
    def chat(self, model: str, messages: list[Message], tools: list[ToolDefinition]) -> LLMResponse:
        """Raise LLMTransientError for a temporary error, LLMError for a final one."""


class LLMClient(Protocol):
    """Interface used by the runner: one entry point for every provider and model."""

    def chat(self, provider: str, model: str, messages: list[Message], tools: list[ToolDefinition]) -> LLMResponse: ...
