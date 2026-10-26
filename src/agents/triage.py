"""Triage agent: identifies the faulty job and classifies the cause of the failure.

First agent of the chain: only failures fixable by changing the repository go on to diagnosis and fixing;
a transient infrastructure problem is simply retried.
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import Field, model_validator

from agent_base.agent_base import AgentConfig
from agents.outputs import AgentOutput
from tools.tags import TAG_CI, TAG_CONTEXT, TAG_OBSERVABILITY

TRIAGE_LLM_PROVIDER = os.getenv("TRIAGE_LLM_PROVIDER", "")
TRIAGE_LLM_MODEL = os.getenv("TRIAGE_LLM_MODEL", "")

Category = Literal["code_bug", "test_failure", "ci_config", "dependency", "infra_flaky", "unknown"]


class TriageOutput(AgentOutput):
    category: Category = Field(..., description="Category of the cause.")
    failed_job_id: int = Field(..., ge=1, description="Most relevant failed job.")
    # Kept short: it is passed on to the next agents
    log_excerpt: str = Field(..., min_length=1, max_length=2000, description="Decisive log excerpt (a few lines).")
    # Gate of the chain: if False, no diagnosis or fix is attempted
    fixable_by_code: bool = Field(..., description="True if a change to the repository can fix the failure.")

    @model_validator(mode="after")
    def category_must_match_fixability(self) -> TriageOutput:
        """A transient infrastructure problem is retried, not fixed by changing code."""
        if self.category == "infra_flaky" and self.fixable_by_code:
            raise ValueError("An infra_flaky failure is not fixable by code: set fixable_by_code to false.")
        return self


class TriageAgent(AgentConfig):
    """The LLM comes from .env (TRIAGE_LLM_PROVIDER, TRIAGE_LLM_MODEL); a fast, inexpensive model is usually enough."""

    agent_name: str = "triage"
    # No code tools
    tool_tags: frozenset[str] = frozenset({TAG_CI, TAG_OBSERVABILITY, TAG_CONTEXT})
    output_model: type[AgentOutput] = TriageOutput
    llm_provider: str = TRIAGE_LLM_PROVIDER
    llm_model: str = TRIAGE_LLM_MODEL
    prompt_system: str = """You are the TRIAGE agent of a CI/CD auto-remediation system.
You are given a failed pipeline. You identify the most relevant failed job and you classify the cause.

Method:
1. Read the log of the failed job(s) (get_job_log). Find the FIRST real error, not its consequences.
2. If an observability tool is available and the log mentions a lack of memory, a timeout
   or an unreachable service, check the metrics and alerts at that time.
3. Classify the cause in ONE category:
   - code_bug: error in the application code (exception, syntax, import, type...);
   - test_failure: a test fails because the code does not do what the test expects;
   - ci_config: error in the CI configuration, a CI script, an image or a variable;
   - dependency: dependency not found, incompatible version, unreachable registry;
   - infra_flaky: transient problem (network, unavailable runner, runner memory);
   - unknown: impossible to determine with the available information.
4. State whether the cause can be fixed by changing files of the repository (fixable_by_code).

Rules:
- Do not invent anything that is not in the logs.
- Quote the decisive log excerpt.
- Write in English.
ALWAYS finish by calling submit_triage."""
