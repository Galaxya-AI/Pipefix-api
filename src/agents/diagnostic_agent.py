"""Diagnosis agent: finds the root cause and prepares a fix plan.

It cross-checks the log, the code, the latest commits and, if the
connectors are enabled, the metrics and alerts (Prometheus, Grafana).
When an error is unknown, it can call the research agent.
This agent does not change anything.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator

from agents.base_agent import AgentConfig
from agents.outputs import AgentOutput


class DiagnosisOutput(AgentOutput):
    """Structured result of the diagnosis agent.

    Parameters
    ----------
    summary : str
        Short conclusion of the diagnosis (inherited from AgentOutput).
    root_cause : str
        Root cause of the failure (the cause, not the symptom).
    files : list[str]
        Repository-relative paths of the files to change (at least one).
    evidence : str
        Log, code or commit excerpts that prove the root cause, with line numbers.
    fix_plan : str
        Minimal fix, described step by step.
    confidence : {"low", "medium", "high"}
        How sure the agent is of its diagnosis.
    sources : list[str]
        URLs returned by the research agent, if it was called.

    Raises
    ------
    pydantic.ValidationError
        If a field is missing, has a wrong type, or breaks one of the rules below.
    """

    root_cause: str = Field(
        ..., min_length=10, description="Root cause of the failure."
    )
    files: list[str] = Field(
        ..., min_length=1, description="Files to change (relative paths)."
    )
    evidence: str = Field(
        ...,
        min_length=10,
        description="Excerpts that prove the cause, with line numbers.",
    )
    fix_plan: str = Field(..., min_length=10, description="Minimal fix, step by step.")
    confidence: Literal["low", "medium", "high"] = Field(
        ..., description="Confidence level."
    )
    sources: list[str] = Field(
        default_factory=list, description="Research URLs used as evidence."
    )

    @field_validator("files")
    @classmethod
    def files_must_be_relative(cls, files: list[str]) -> list[str]:
        """Refuse absolute paths and paths that leave the repository.

        Parameters
        ----------
        files : list[str]
            Paths proposed by the LLM.

        Returns
        -------
        list[str]
            The same paths, without duplicates, in their original order.

        Raises
        ------
        ValueError
            If a path is absolute or contains "..".
        """
        path: str  # Current path.
        for path in files:  # Check every path.
            if path.startswith("/") or ".." in path.split(
                "/"
            ):  # Absolute or escaping the repository…
                raise ValueError(
                    f"File path must be relative to the repository: {path}"
                )  # …refused.
        return list(dict.fromkeys(files))  # Remove duplicates, keep order.

    @field_validator("sources")
    @classmethod
    def sources_must_be_urls(cls, sources: list[str]) -> list[str]:
        """Accept only http(s) URLs as research sources.

        Parameters
        ----------
        sources : list[str]
            URLs proposed by the LLM.

        Returns
        -------
        list[str]
            The validated URLs.

        Raises
        ------
        ValueError
            If a source is not an http(s) URL.
        """
        url: str  # Current URL.
        for url in sources:  # Check every source.
            if not url.startswith(("http://", "https://")):  # Not a web address…
                raise ValueError(f"Source must be an http(s) URL: {url}")  # …refused.
        return sources  # Valid sources.


class DiagnosticAgent(AgentConfig):
    """Diagnosis agent configuration.

    All fields have fixed values except the LLM, which comes from .env
    (DIAGNOSIS_LLM_PROVIDER and DIAGNOSIS_LLM_MODEL).

    Parameters
    ----------
    agent_name : str, default "diagnosis"
        Identifier used in logs, reports and routing.
    prompt_system : str, default SYSTEM_PROMPT
        Role, method and rules of the agent.
    tool_tags : frozenset[str], default {"ci", "code", "observability", "context"}
        Read-only tools the agent receives.
    sub_agents : list[str], default ["research"]
        Agents it can call as tools.
    output_model : type[AgentOutput], default DiagnosisOutput
        Model that validates its final answer.
    llm_provider : str
        LLM provider, read from .env (required).
    llm_model : str
        LLM model, read from .env (required).

    Examples
    --------
    >>> diagnosis = DiagnosticAgent(llm_provider="openai_compatible", llm_model="mistral-large-latest")
    >>> diagnosis.finish_tool_name
    'submit_diagnosis'
    """

    agent_name: str = "diagnosis"
    tool_tags: frozenset[str] = frozenset({"ci", "code", "observability", "context"})
    sub_agents: list[str] = Field(default_factory=lambda: ["research"])
    output_model: type[AgentOutput] = DiagnosisOutput
    prompt_system: str = """
    
        You are the DIAGNOSIS agent of a CI/CD auto-remediation system.
        You are given the triage result of a failed pipeline. You find the ROOT CAUSE, without changing anything.

        Method:
        1. Re-read the exact error and the stack trace (get_job_log if needed).
        2. Locate the relevant code: search_code to find a function or a message, then read_file.
        3. Check the latest commits (get_recent_commits, get_commit_changes): the error often comes from the latest change.
        4. If observability tools are available and relevant, check the metrics or alerts.
        5. If the error is unknown to you (new library version, unusual message), call the research agent.
        6. Distinguish the cause (e.g. wrong operator in add()) from the symptom (e.g. test_add fails).

        Rules:
        - Back every statement with something you saw (log line, code line, commit, research source).
        - Propose the MINIMAL fix. Do not propose changing a test to make it pass,
        unless the test itself is clearly wrong; in that case, say so explicitly.
        - If you are not sure, say so with confidence = low.
        - Write in English.
        ALWAYS finish by calling submit_diagnosis.
        
        """
