"""Fixer agent: applies the fix plan as proposed changes.

It publishes nothing: its changes stay in the changeset until the review.
"""

from __future__ import annotations

import os

from pydantic import Field, field_validator, model_validator

from agent_base.agent_base import AgentConfig
from agents.diagnostic_agent import check_relative_paths
from agents.outputs import AgentOutput
from tools.tags import TAG_CODE, TAG_EDIT, TAG_REVIEW

FIXER_LLM_PROVIDER = os.getenv("FIXER_LLM_PROVIDER", "")
FIXER_LLM_MODEL = os.getenv("FIXER_LLM_MODEL", "")

TEST_PREFIXES = ("tests/", "test/")


class FixOutput(AgentOutput):
    files_changed: list[str] = Field(..., min_length=1, description="Changed files (relative paths).")
    # Safety signal flagged to the reviewer and in the MR: weakening a test can hide a bug instead of fixing it
    tests_modified: bool = Field(..., description="True if a test file was changed.")

    @field_validator("files_changed")
    @classmethod
    def files_must_be_relative(cls, files: list[str]) -> list[str]:
        return check_relative_paths(files)

    @model_validator(mode="after")
    def tests_flag_must_match_files(self) -> FixOutput:
        touches_tests = any(path.startswith(TEST_PREFIXES) for path in self.files_changed)
        if touches_tests and not self.tests_modified:
            raise ValueError("A test file was changed: tests_modified must be true.")
        return self


class FixerAgent(AgentConfig):
    """The LLM comes from .env (FIXER_LLM_PROVIDER, FIXER_LLM_MODEL)."""

    agent_name: str = "fixer"
    # No publishing tool
    tool_tags: frozenset[str] = frozenset({TAG_CODE, TAG_EDIT, TAG_REVIEW})
    output_model: type[AgentOutput] = FixOutput
    llm_provider: str = FIXER_LLM_PROVIDER
    llm_model: str = FIXER_LLM_MODEL
    prompt_system: str = """You are the FIXER agent of a CI/CD auto-remediation system.
You are given a diagnosis. You propose the MINIMAL fix with propose_edit.

Method:
1. Read each file to change with read_file(raw=true) to get the EXACT content.
2. Call propose_edit with an old_text copied exactly (spaces, indentation, no line numbers)
   and long enough to be unique in the file.
3. Check the result with view_changeset.
4. If a previous review reported problems, fix them.
5. The diagnosis may list several causes: fix ALL of them in this attempt, not only the first,
   so that the next pipeline does not stop on an error already known.

Rules:
- For each cause, change as few lines as possible; keep the style of the file.
- Never delete a test and never weaken an assertion to make a pipeline pass.
- Do not touch the CI configuration unless the diagnosis requires it (ci_config category).
- Do not add any secret, password or token.
- The failed jobs (lint, tests, type check...) are constraints to meet TOGETHER: the change must
  satisfy all of them, not fix one job and break another.
- Imports: before removing an import reported as unused, check that the name is not used in the
  file or elsewhere (search_code), including in your own changes, __all__, string lookups
  (globals(), getattr, importlib) and imports kept for a side effect. When you use a name,
  make sure it is imported. If a lint error and a runtime use disagree, keep the runtime
  behaviour and fix the lint error explicitly (use the name, or a "# noqa: F401" comment
  explaining why the import stays).
- Write your summary in English.
ALWAYS finish by calling submit_fixer."""
