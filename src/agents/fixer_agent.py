"""Fixer agent: applies the fix plan as proposed changes.

It receives the diagnosis (and, on a new attempt, the reviewer's comments)
and proposes the smallest possible change with propose_edit. It does not
publish anything: its changes stay in the changeset until the review.
"""

# Lazy annotations: lets a class name be used inside its own methods.
from __future__ import annotations

from agent_base.base_agent import AgentConfig
from agents.outputs import AgentOutput
from pydantic import (
    Field,
    field_validator,
    model_validator,
)

TEST_PREFIXES: tuple[str, ...] = ("tests/", "test/")


class FixOutput(AgentOutput):
    """Structured result of the fixer agent.

    Parameters
    ----------
    summary : str
        What was fixed, in one or two sentences (inherited from AgentOutput).
    files_changed : list[str]
        Repository-relative paths of the changed files (at least one).
    tests_modified : bool
        True if at least one test file was changed. Used as a safety signal:
        test changes are flagged for the reviewer and in the merge request,
        because weakening a test can hide a bug instead of fixing it.

    Raises
    ------
    pydantic.ValidationError
        If a field is missing, has a wrong type, or breaks one of the rules below.
    """

    # Files changed by the fix: at least one, relative to the repository.
    files_changed: list[str] = Field(
        ..., min_length=1, description="Changed files (relative paths)."
    )

    # Safety signal: a fixer could make a pipeline green by weakening or deleting a test
    # instead of fixing the code. The fixer must declare any test change, so that the
    # reviewer checks it carefully, the merge request shows a warning, and the
    # orchestrator can require a human review. The model validator below refuses a
    # fix that changes a file under tests/ while declaring tests_modified = False.
    tests_modified: bool = Field(..., description="True if a test file was changed.")

    # Check this field alone, before the object exists (hence cls, not self).
    @field_validator("files_changed")
    @classmethod
    def files_must_be_relative(cls, files: list[str]) -> list[str]:
        """Refuse absolute paths and paths that leave the repository.

        Parameters
        ----------
        files : list[str]
            Paths declared by the LLM.

        Returns
        -------
        list[str]
            The same paths, without duplicates, in their original order.

        Raises
        ------
        ValueError
            If a path is absolute or contains "..".
        """
        # Current path in the loop.
        path: str
        # Check every path declared by the LLM.
        for path in files:
            # Absolute path, or ".." that escapes the repository.
            if path.startswith("/") or ".." in path.split("/"):
                # Refuse the path; the message is sent back to the LLM.
                raise ValueError(
                    f"File path must be relative to the repository: {path}"
                )
        # Remove duplicates while keeping the original order.
        return list(dict.fromkeys(files))

    # Check the whole object, after every field is validated (hence self).
    @model_validator(mode="after")
    def tests_flag_must_match_files(self) -> FixOutput:
        """Refuse a fix that changes a test file but declares tests_modified = False.

        Returns
        -------
        FixOutput
            The validated output.

        Raises
        ------
        ValueError
            If a changed file is a test and tests_modified is False.
        """
        # True if at least one changed file is under a test folder.
        touches_tests: bool = any(
            path.startswith(TEST_PREFIXES) for path in self.files_changed
        )
        # A test was changed but not declared: hidden test change.
        if touches_tests and not self.tests_modified:
            # Refuse the output; the message is sent back to the LLM.
            raise ValueError("A test file was changed: tests_modified must be true.")
        # Consistent output.
        return self


class FixerAgent(AgentConfig):
    """Fixer agent configuration.

    All fields have fixed values except the LLM, which comes from .env
    (FIXER_LLM_PROVIDER and FIXER_LLM_MODEL).

    Parameters
    ----------
    agent_name : str, default "fixer"
        Identifier used in logs, reports and routing.
    prompt_system : str
        Role, method and rules of the agent.
    tool_tags : frozenset[str], default {"code", "edit", "review"}
        Code reading, edit proposal and diff viewing tools.
    output_model : type[AgentOutput], default FixOutput
        Model that validates its final answer.
    llm_provider : str
        LLM provider, read from .env (required).
    llm_model : str
        LLM model, read from .env (required).

    Examples
    --------
    >>> fixer = FixerAgent(llm_provider="anthropic", llm_model="claude-sonnet-...")
    >>> fixer.finish_tool_name
    'submit_fixer'
    """

    # Unique name; the finish tool is "submit_fixer".
    agent_name: str = "fixer"
    # Tools: read code, propose edits, view the diff. No publishing tool.
    tool_tags: frozenset[str] = frozenset({"code", "edit", "review"})
    # Model that validates the final answer.
    output_model: type[AgentOutput] = FixOutput
    # Permanent instructions, sent as the system message.
    prompt_system: str = """
        You are the FIXER agent of a CI/CD auto-remediation system.
        You are given a diagnosis. You propose the MINIMAL fix with propose_edit.

        Method:
        1. Read each file to change with read_file(raw=true) to get the EXACT content.
        2. Call propose_edit with an old_text copied exactly (spaces, indentation, no line numbers)
        and long enough to be unique in the file.
        3. Check the result with view_changeset.
        4. If a previous review reported problems, fix them.

        Rules:
        - Change as few lines as possible; keep the style of the file.
        - Never delete a test and never weaken an assertion to make a pipeline pass.
        - Do not touch the CI configuration unless the diagnosis requires it (ci_config category).
        - Do not add any secret, password or token.
        - Write your summary in English.
        ALWAYS finish by calling submit_fixer.
    """
