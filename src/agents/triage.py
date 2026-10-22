"""Triage agent: identifies the faulty job and classifies the cause of the failure.

It is the first agent of the chain. Its result decides what happens next:
only failures that can be fixed by changing the repository go on to
diagnosis and fixing; a transient infrastructure problem can simply be
retried; dependency or CI errors may first go to the research agent.
"""

from __future__ import annotations

from typing import Literal

from agent_base.agent_base import AgentConfig
from agents.outputs import AgentOutput
from pydantic import (
    Field,
    model_validator,
)

Category = Literal[
    "code_bug", "test_failure", "ci_config", "dependency", "infra_flaky", "unknown"
]


class TriageOutput(AgentOutput):
    """Structured result of the triage agent.

    Parameters
    ----------
    summary : str
        Short explanation of the failure (inherited from AgentOutput).
    category : {"code_bug", "test_failure", "ci_config", "dependency", "infra_flaky", "unknown"}
        Cause of the failure.
    failed_job_id : int
        Identifier of the most relevant failed job.
    log_excerpt : str
        Decisive log lines (the first real error, not its consequences).
    fixable_by_code : bool
        True if a change to the repository can fix the failure.

    Raises
    ------
    pydantic.ValidationError
        If a field is missing, has a wrong type, or breaks one of the rules below.
    """

    # Cause of the failure: decides the next step (stop, retry, research or diagnosis).
    category: Category = Field(..., description="Category of the cause.")

    # Most relevant failed job; GitLab job ids are positive integers.
    failed_job_id: int = Field(..., ge=1, description="Most relevant failed job.")

    # Proof of the classification: a few log lines, kept short for the next agents.
    log_excerpt: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Decisive log excerpt (a few lines).",
    )

    # Gate of the chain: if False, no diagnosis or fix is attempted.
    fixable_by_code: bool = Field(
        ..., description="True if a change to the repository can fix the failure."
    )

    # Check the whole object, after every field is validated (hence self).
    @model_validator(mode="after")
    def category_must_match_fixability(self) -> TriageOutput:
        """Refuse a classification that contradicts the fixability flag.

        A transient infrastructure problem (runner down, network timeout) is
        not fixed by changing code: it is retried. Declaring it fixable would
        send the fixer to change code that is not broken.

        Returns
        -------
        TriageOutput
            The validated output.

        Raises
        ------
        ValueError
            If the category is infra_flaky and fixable_by_code is True.
        """
        # Infrastructure problem declared as fixable by code: contradiction.
        if self.category == "infra_flaky" and self.fixable_by_code:
            # Refuse the output; the message is sent back to the LLM.
            raise ValueError(
                "An infra_flaky failure is not fixable by code: set fixable_by_code to false."
            )
        # Consistent classification.
        return self


class TriageAgent(AgentConfig):
    """Triage agent configuration.

    All fields have fixed values except the LLM, which comes from .env
    (TRIAGE_LLM_PROVIDER and TRIAGE_LLM_MODEL). A fast, inexpensive model
    is usually enough for this task.

    Parameters
    ----------
    agent_name : str, default "triage"
        Identifier used in logs, reports and routing.
    prompt_system : str
        Role, method and rules of the agent.
    tool_tags : frozenset[str], default {"ci", "observability", "context"}
        Job log, metrics and alerts reading tools.
    output_model : type[AgentOutput], default TriageOutput
        Model that validates its final answer.
    llm_provider : str
        LLM provider, read from .env (required).
    llm_model : str
        LLM model, read from .env (required).

    Examples
    --------
    >>> triage = TriageAgent(llm_provider="openai_compatible", llm_model="mistral-small-latest")
    >>> triage.finish_tool_name
    'submit_triage'
    """

    # Unique name; the finish tool is "submit_triage".
    agent_name: str = "triage"
    # Tools: read job logs, and metrics or alerts if the connectors are enabled. No code tools.
    tool_tags: frozenset[str] = frozenset({"ci", "observability", "context"})
    # Model that validates the final answer.
    output_model: type[AgentOutput] = TriageOutput
    # Permanent instructions, sent as the system message.
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
