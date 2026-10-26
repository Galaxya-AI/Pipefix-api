"""Creates every agent of the system; a missing LLM setting in .env fails here, at start-up."""

from __future__ import annotations

from agent_base.agent_base import AgentConfig
from agents.diagnostic_agent import DiagnosticAgent
from agents.fixer_agent import FixerAgent
from agents.research_agent import ResearchAgent
from agents.reviewer import ReviewerAgent
from agents.triage import TriageAgent


def build_agents() -> dict[str, AgentConfig]:
    """Return the agents by name; raise ValueError if an agent lists an unknown sub-agent."""
    agents: list[AgentConfig] = [TriageAgent(), DiagnosticAgent(), FixerAgent(), ReviewerAgent(), ResearchAgent()]
    by_name = {agent.agent_name: agent for agent in agents}
    for agent in agents:
        for sub_agent in agent.sub_agents:
            if sub_agent not in by_name:
                raise ValueError(f"Agent '{agent.agent_name}' calls unknown sub-agent '{sub_agent}'.")
    return by_name
