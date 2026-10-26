"""Diagnosis agent: finds the root cause and prepares a fix plan, without changing anything.

When an error is unknown, it can call the research agent.
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import Field, field_validator

from agent_base.agent_base import AgentConfig
from agents.outputs import AgentOutput
from tools.tags import TAG_CI, TAG_CODE, TAG_CONTEXT, TAG_OBSERVABILITY

DIAGNOSIS_LLM_PROVIDER = os.getenv("DIAGNOSIS_LLM_PROVIDER", "")
DIAGNOSIS_LLM_MODEL = os.getenv("DIAGNOSIS_LLM_MODEL", "")


def check_relative_paths(paths: list[str]) -> list[str]:
    """Refuse absolute paths and paths that leave the repository; return the paths deduplicated, in order."""
    for path in paths:
        if path.startswith("/") or ".." in path.split("/"):
            raise ValueError(f"File path must be relative to the repository: {path}")
    return list(dict.fromkeys(paths))


class DiagnosisOutput(AgentOutput):
    root_cause: str = Field(..., min_length=10, description="Root cause of the failure.")
    files: list[str] = Field(..., min_length=1, description="Files to change (relative paths).")
    evidence: str = Field(..., min_length=10, description="Excerpts that prove the cause, with line numbers.")
    fix_plan: str = Field(..., min_length=10, description="Minimal fix, step by step.")
    confidence: Literal["low", "medium", "high"] = Field(..., description="Confidence level.")
    sources: list[str] = Field(default_factory=list, description="Research URLs used as evidence.")

    @field_validator("files")
    @classmethod
    def files_must_be_relative(cls, files: list[str]) -> list[str]:
        return check_relative_paths(files)

    @field_validator("sources")
    @classmethod
    def sources_must_be_urls(cls, sources: list[str]) -> list[str]:
        for url in sources:
            if not url.startswith(("http://", "https://")):
                raise ValueError(f"Source must be an http(s) URL: {url}")
        return sources


class DiagnosticAgent(AgentConfig):
    """The LLM comes from .env (DIAGNOSIS_LLM_PROVIDER, DIAGNOSIS_LLM_MODEL)."""

    agent_name: str = "diagnosis"
    # Read-only tools
    tool_tags: frozenset[str] = frozenset({TAG_CI, TAG_CODE, TAG_OBSERVABILITY, TAG_CONTEXT})
    sub_agents: list[str] = Field(default_factory=lambda: ["research"])
    output_model: type[AgentOutput] = DiagnosisOutput
    llm_provider: str = DIAGNOSIS_LLM_PROVIDER
    llm_model: str = DIAGNOSIS_LLM_MODEL
    prompt_system: str = """You are the DIAGNOSIS agent of a CI/CD auto-remediation system.
You are given the triage result of a failed pipeline. You find the ROOT CAUSE, without changing anything.

Method:
1. Re-read the exact error and the stack trace (get_job_log if needed).
2. Locate the relevant code: search_code to find a function or a message, then read_file.
3. Check the latest commits (get_recent_commits, get_commit_changes): the error often comes from the latest change.
4. If observability tools are available and relevant, check the metrics or alerts.
5. If the error is unknown to you (new library version, unusual message), call the research agent (call_research).
6. Distinguish the cause (e.g. wrong operator in add()) from the symptom (e.g. test_add fails).

Rules:
- Back every statement with something you saw (log line, code line, commit, research source).
- Propose the MINIMAL fix. Do not propose changing a test to make it pass,
  unless the test itself is clearly wrong; in that case, say so explicitly.
- If you are not sure, say so with confidence = low.
- Write in English.
ALWAYS finish by calling submit_diagnosis."""
