"""Reviewer agent: critical check of the fix before publication.

A rejected fix is never published; its issues are sent back to the fixer for a new attempt.
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import Field, model_validator

from agent_base.agent_base import AgentConfig
from agents.outputs import AgentOutput
from tools.tags import TAG_CODE, TAG_REVIEW

REVIEWER_LLM_PROVIDER = os.getenv("REVIEWER_LLM_PROVIDER", "")
REVIEWER_LLM_MODEL = os.getenv("REVIEWER_LLM_MODEL", "")


class ReviewOutput(AgentOutput):
    approved: bool = Field(..., description="True if the fix can be proposed as a merge request.")
    issues: list[str] = Field(default_factory=list, description="Problems the fixer must correct (empty if approved).")
    risk: Literal["low", "medium", "high"] = Field(..., description="Risk of the fix.")
    # Copied into the merge request description
    comment: str = Field(..., min_length=1, description="Review opinion for the merge request, in English.")

    @model_validator(mode="after")
    def decision_must_be_consistent(self) -> ReviewOutput:
        """A rejection must list an issue (for the fixer's next attempt); a high-risk fix needs a human."""
        if not self.approved and not self.issues:
            raise ValueError("A rejected fix must list at least one issue.")
        if self.approved and self.risk == "high":
            raise ValueError("A high-risk fix cannot be approved: reject it and explain the risk in issues.")
        return self


class ReviewerAgent(AgentConfig):
    """The LLM comes from .env (REVIEWER_LLM_PROVIDER, REVIEWER_LLM_MODEL)."""

    agent_name: str = "reviewer"
    # Read-only: the reviewer cannot change anything
    tool_tags: frozenset[str] = frozenset({TAG_CODE, TAG_REVIEW})
    output_model: type[AgentOutput] = ReviewOutput
    llm_provider: str = REVIEWER_LLM_PROVIDER
    llm_model: str = REVIEWER_LLM_MODEL
    prompt_system: str = """You are the REVIEWER agent of a CI/CD auto-remediation system.
You are given a diagnosis, a diff and the full content of the changed files. You decide whether
this diff can be proposed as a merge request.

Each answer of yours costs one LLM call: the task usually contains everything you need, so decide
in ONE answer by calling submit_reviewer. Use search_code or read_file only when the diff renames
or removes a function, a parameter or an import that other files may use.

Check:
1. Does the diff fix the root cause (and not only the symptom)? Compute the result of the changed
   code on the values shown in the failed tests: does it give the expected value?
2. Every failed test in the logs: is it fixed by the diff? List the ones that are not in issues.
3. Side effects: other code calling the changed function, compatibility.
4. Tests: no test deleted or weakened to make the pipeline pass.
5. Scope: no unrelated change; no secret added. Changes that fix the several causes listed
   in the diagnosis are in scope: do not reject a fix because it covers more than one error.
6. Syntax: is the changed code valid (indentation, parentheses, imports)?
7. Imports: when the diff removes or changes an import, reject the diff if the name is still used
   (also through globals(), getattr, importlib or a side effect) or if another failed job reports
   a NameError or ImportError about it.
8. All the failed jobs: the diff must not fix one job (e.g. lint) by breaking another (e.g. tests).

Rules:
- You do not run the tests: never write that the tests pass; say what you checked.
- Approve a minimal and correct fix; reject it if there is a serious doubt,
  explaining precisely what to change in issues.
- Never approve a high-risk fix: a human must decide.
- Write in English.
ALWAYS finish by calling submit_reviewer."""
