"""Research agent: searches the web for an unknown error.

Never run by the workflow directly: the diagnosis agent calls it as a tool (call_research).
"""

from __future__ import annotations

import os

from pydantic import Field, field_validator

from agent_base.agent_base import AgentConfig
from agents.outputs import AgentOutput
from tools.tags import TAG_RESEARCH

RESEARCH_LLM_PROVIDER = os.getenv("RESEARCH_LLM_PROVIDER", "")
RESEARCH_LLM_MODEL = os.getenv("RESEARCH_LLM_MODEL", "")


class ResearchOutput(AgentOutput):
    findings: list[str] = Field(default_factory=list, description="Facts found, one per item.")
    sources: list[str] = Field(default_factory=list, description="URLs of the pages used.")

    @field_validator("sources")
    @classmethod
    def sources_must_be_urls(cls, sources: list[str]) -> list[str]:
        for url in sources:
            if not url.startswith(("http://", "https://")):
                raise ValueError(f"Source must be an http(s) URL: {url}")
        return sources


class ResearchAgent(AgentConfig):
    """The LLM comes from .env (RESEARCH_LLM_PROVIDER, RESEARCH_LLM_MODEL)."""

    agent_name: str = "research"
    tool_tags: frozenset[str] = frozenset({TAG_RESEARCH})
    output_model: type[AgentOutput] = ResearchOutput
    llm_provider: str = RESEARCH_LLM_PROVIDER
    llm_model: str = RESEARCH_LLM_MODEL
    prompt_system: str = """You are the RESEARCH agent of a CI/CD auto-remediation system.
Another agent asks you a question about an error it does not know (message, library version, tool behaviour).

Method:
1. Search the web with precise queries (exact error message, library name and version).
2. Read the most relevant pages (official documentation, changelogs, issue trackers first).
3. Keep only facts that answer the question.

Rules:
- Never invent a fact or a URL: every finding must come from a page you read.
- Give the URL of every page you used in sources.
- Never include secrets or private code in your queries.
- Write in English.
ALWAYS finish by calling submit_research."""
