"""Workflow transitions: which step comes after each agent.

Fixed order: triage -> diagnosis -> fixer <-> reviewer -> publication; the
diagnosis agent may call the research agent itself, as a tool. These rules,
not an LLM, decide the order.
"""

from __future__ import annotations

from agents.diagnostic_agent import DiagnosisOutput
from agents.fixer_agent import FixOutput
from agents.outputs import AgentOutput
from agents.reviewer import ReviewOutput
from agents.triage import TriageOutput

STEP_PUBLISH = "publish"

# Final status: transient infrastructure failure, nothing to fix in the code
STATUS_INFRA_FLAKY = "infra_flaky"

STATUS_NOT_FIXABLE = "not_fixable_by_code"


def next_step(output: AgentOutput) -> str:
    """Return the next agent, STEP_PUBLISH, or a final status.

    Raises ValueError for an output type without a rule (e.g. the research agent).
    """
    if isinstance(output, TriageOutput):
        # Transient problems are retried, not fixed
        if output.category == "infra_flaky":
            return STATUS_INFRA_FLAKY
        if not output.fixable_by_code:
            return STATUS_NOT_FIXABLE
        return "diagnosis"
    if isinstance(output, DiagnosisOutput):
        return "fixer"
    if isinstance(output, FixOutput):
        return "reviewer"
    if isinstance(output, ReviewOutput):
        # A rejected fix goes back to the fixer with the review issues
        return STEP_PUBLISH if output.approved else "fixer"
    raise ValueError(f"No workflow rule after {type(output).__name__}.")
