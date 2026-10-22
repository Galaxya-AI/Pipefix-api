"""Reviewer agent: critical check of the fix before publication.

It is the automatic safeguard of the system: it reads the diagnosis and
the diff, and decides whether the fix can be proposed as a merge request.
A rejected fix is never published; its issues are sent back to the fixer
for a new attempt.
"""

from __future__ import annotations

from typing import Literal

from agent_base.agent_base import AgentConfig
from agents.outputs import AgentOutput
from pydantic import (
    Field,
    model_validator,
)


class ReviewOutput(AgentOutput):
    """Structured result of the reviewer agent.

    Parameters
    ----------
    summary : str
        Review opinion in one or two sentences (inherited from AgentOutput).
    approved : bool
        True if the fix can be proposed as a merge request.
    issues : list[str]
        Problems the fixer must correct (empty if approved).
    risk : {"low", "medium", "high"}
        Risk of the fix if it were merged.
    comment : str
        Review opinion written into the merge request description.

    Raises
    ------
    pydantic.ValidationError
        If a field is missing, has a wrong type, or breaks one of the rules below.
    """

    # Final decision: True means the fix can be published as a draft merge request.
    approved: bool = Field(
        ..., description="True if the fix can be proposed as a merge request."
    )

    # Problems to fix, sent back to the fixer when the fix is rejected.
    issues: list[str] = Field(
        default_factory=list,
        description="Problems the fixer must correct (empty if approved).",
    )

    # Risk of the fix; a high-risk fix is never approved automatically (see validator).
    risk: Literal["low", "medium", "high"] = Field(..., description="Risk of the fix.")

    # Review opinion, copied into the merge request so that a human can read it.
    comment: str = Field(
        ...,
        min_length=1,
        description="Review opinion for the merge request, in English.",
    )

    # Check the whole object, after every field is validated (hence self).
    @model_validator(mode="after")
    def decision_must_be_consistent(self) -> ReviewOutput:
        """Refuse an incoherent review decision.

        Two rules:
        - a rejection must say why (at least one issue), otherwise the fixer
          cannot improve its next attempt;
        - a high-risk fix cannot be approved automatically: it needs a human.

        Returns
        -------
        ReviewOutput
            The validated output.

        Raises
        ------
        ValueError
            If the fix is rejected without issues, or approved with a high risk.
        """
        # Rejected without any reason: the fixer would not know what to change.
        if not self.approved and not self.issues:
            # Refuse the output; the message is sent back to the LLM.
            raise ValueError("A rejected fix must list at least one issue.")
        # Approved although the risk is high: automatic approval is not allowed.
        if self.approved and self.risk == "high":
            # Refuse the output; the message is sent back to the LLM.
            raise ValueError(
                "A high-risk fix cannot be approved: reject it and explain the risk in issues."
            )
        # Consistent decision.
        return self


class ReviewerAgent(AgentConfig):
    """Reviewer agent configuration.

    All fields have fixed values except the LLM, which comes from .env
    (REVIEWER_LLM_PROVIDER and REVIEWER_LLM_MODEL).

    Parameters
    ----------
    agent_name : str, default "reviewer"
        Identifier used in logs, reports and routing.
    prompt_system : str
        Role, checks and rules of the agent.
    tool_tags : frozenset[str], default {"code", "review"}
        Code and diff reading tools only: the reviewer cannot change anything.
    output_model : type[AgentOutput], default ReviewOutput
        Model that validates its final answer.
    llm_provider : str
        LLM provider, read from .env (required).
    llm_model : str
        LLM model, read from .env (required).

    Examples
    --------
    >>> reviewer = ReviewerAgent(llm_provider="anthropic", llm_model="claude-sonnet-...")
    >>> reviewer.finish_tool_name
    'submit_reviewer'
    """

    # Unique name; the finish tool is "submit_reviewer".
    agent_name: str = "reviewer"
    # Tools: read the code and view the diff. No edit tool, so the reviewer cannot change the fix.
    tool_tags: frozenset[str] = frozenset({"code", "review"})
    # Model that validates the final answer.
    output_model: type[AgentOutput] = ReviewOutput
