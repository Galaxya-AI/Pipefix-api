"""Policy check: applies the data policy to a project before anything is read.

A confidential project may only be processed when every agent uses an
internal LLM provider; web research is then disabled. The check only reads
the project metadata, never its code or logs.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from agent_base.agent_base import AgentConfig
from journal.run_journal import RunJournal
from policy.data_policy import DataPolicy
from workflow_remediation.ports import CodePlatform

# Status of a confidential project that would reach an external LLM
STATUS_BLOCKED = "blocked_by_policy"


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    allow_research: bool
    # Why the run is blocked, when it is
    reason: str = ""


def check_project_policy(
    platform: CodePlatform,
    policy: DataPolicy,
    agents: Iterable[AgentConfig],
    project: str,
    journal: RunJournal,
) -> PolicyDecision:
    """Decide whether a project may be processed, and whether web research is allowed; journal the decision."""
    if not policy.confidential_projects:
        return PolicyDecision(allowed=True, allow_research=True)
    # Metadata only: no code or log is read before the decision
    info = platform.get_project(project)
    if not policy.is_confidential([project, info.get("path_with_namespace"), info.get("id")]):
        return PolicyDecision(allowed=True, allow_research=True)
    external = policy.external_providers(agent.llm_provider for agent in agents)
    if external:
        journal.log("policy", "blocked", {"project": project, "external_providers": external})
        return PolicyDecision(
            allowed=False,
            allow_research=False,
            reason=f"Confidential project: providers {external} are not in INTERNAL_LLM_PROVIDERS.",
        )
    journal.log("policy", "confidential_project", {"project": project, "web_research": False})
    return PolicyDecision(allowed=True, allow_research=False)
