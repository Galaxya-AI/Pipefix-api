"""Limits of an agent run, read from the environment (.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass

AGENT_MAX_TURNS = int(os.getenv("AGENT_MAX_TURNS", "10"))
# Invalid final answers accepted before the run is stopped
AGENT_MAX_INVALID_ANSWERS = int(os.getenv("AGENT_MAX_INVALID_ANSWERS", "2"))
AGENT_MAX_SUB_AGENT_DEPTH = int(os.getenv("AGENT_MAX_SUB_AGENT_DEPTH", "2"))
# Cost guard: LLM calls for a whole run, all agents together
MAX_LLM_CALLS_PER_RUN = int(os.getenv("MAX_LLM_CALLS_PER_RUN", "60"))


@dataclass(frozen=True)
class RunnerLimits:
    """Limits of one agent run; raises ValueError if a limit is below its minimum."""

    max_turns: int = AGENT_MAX_TURNS
    max_invalid_answers: int = AGENT_MAX_INVALID_ANSWERS
    max_sub_agent_depth: int = AGENT_MAX_SUB_AGENT_DEPTH
    max_llm_calls: int = MAX_LLM_CALLS_PER_RUN

    def __post_init__(self) -> None:
        if self.max_turns < 1:
            raise ValueError(f"AGENT_MAX_TURNS must be at least 1, got {self.max_turns}.")
        if self.max_invalid_answers < 0:
            raise ValueError(f"AGENT_MAX_INVALID_ANSWERS must be at least 0, got {self.max_invalid_answers}.")
        if self.max_sub_agent_depth < 0:
            raise ValueError(f"AGENT_MAX_SUB_AGENT_DEPTH must be at least 0, got {self.max_sub_agent_depth}.")
        if self.max_llm_calls < 1:
            raise ValueError(f"MAX_LLM_CALLS_PER_RUN must be at least 1, got {self.max_llm_calls}.")
