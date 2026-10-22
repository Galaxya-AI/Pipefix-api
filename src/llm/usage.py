"""Usage of the LLM: number of calls and tokens, per agent and for the whole run.

The counts are used for the run report (cost per agent) and to enforce a
global call or token budget.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from llm.base import LLMResponse


@dataclass
class UsageTracker:
    """Count LLM calls and tokens, per agent and for the whole run.

    Parameters
    ----------
    calls : dict[str, int]
        Number of LLM calls per agent name.
    input_tokens : dict[str, int]
        Input tokens per agent name.
    output_tokens : dict[str, int]
        Output tokens per agent name.
    """

    # LLM calls per agent
    calls: dict[str, int] = field(default_factory=dict)
    # Input tokens per agent
    input_tokens: dict[str, int] = field(default_factory=dict)
    # Output tokens per agent
    output_tokens: dict[str, int] = field(default_factory=dict)

    def add(self, agent_name: str, response: LLMResponse) -> None:
        """Record one LLM call of an agent.

        Parameters
        ----------
        agent_name : str
            Agent that made the call.
        response : LLMResponse
            Answer returned by the LLM, with its token counts.
        """
        # One more call for this agent
        self.calls[agent_name] = self.calls.get(agent_name, 0) + 1
        # Add the input tokens of this call
        self.input_tokens[agent_name] = self.input_tokens.get(agent_name, 0) + response.input_tokens
        # Add the output tokens of this call
        self.output_tokens[agent_name] = self.output_tokens.get(agent_name, 0) + response.output_tokens

    def total_calls(self) -> int:
        """Return the number of LLM calls of the whole run.

        Returns
        -------
        int
            Calls of every agent.
        """
        # Sum over all agents
        return sum(self.calls.values())

    def total_tokens(self) -> int:
        """Return the number of input and output tokens of the whole run.

        Returns
        -------
        int
            Input plus output tokens of every agent.
        """
        # Input tokens plus output tokens, over all agents
        return sum(self.input_tokens.values()) + sum(self.output_tokens.values())

    def as_dict(self) -> dict[str, dict[str, int]]:
        """Return the counts per agent, for the run report.

        Returns
        -------
        dict[str, dict[str, int]]
            For each agent: its calls, input tokens and output tokens.
        """
        # One entry per agent that made at least one call
        return {
            name: {
                "calls": self.calls[name],
                "input_tokens": self.input_tokens.get(name, 0),
                "output_tokens": self.output_tokens.get(name, 0),
            }
            for name in self.calls
        }
