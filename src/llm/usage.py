"""LLM calls and tokens per agent, for the run report and the global call budget."""

from __future__ import annotations

from dataclasses import dataclass, field

from llm.base import LLMResponse


@dataclass
class UsageTracker:
    calls: dict[str, int] = field(default_factory=dict)
    input_tokens: dict[str, int] = field(default_factory=dict)
    output_tokens: dict[str, int] = field(default_factory=dict)

    def add(self, agent_name: str, response: LLMResponse) -> None:
        self.calls[agent_name] = self.calls.get(agent_name, 0) + 1
        self.input_tokens[agent_name] = self.input_tokens.get(agent_name, 0) + response.input_tokens
        self.output_tokens[agent_name] = self.output_tokens.get(agent_name, 0) + response.output_tokens

    def total_calls(self) -> int:
        return sum(self.calls.values())

    def total_tokens(self) -> int:
        return sum(self.input_tokens.values()) + sum(self.output_tokens.values())

    def as_dict(self) -> dict[str, dict[str, int]]:
        return {
            name: {
                "calls": self.calls[name],
                "input_tokens": self.input_tokens.get(name, 0),
                "output_tokens": self.output_tokens.get(name, 0),
            }
            for name in self.calls
        }
