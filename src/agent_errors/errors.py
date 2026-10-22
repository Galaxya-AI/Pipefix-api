"""Exceptions raised while running agents."""


class AgentRunError(RuntimeError):
    """Raised when an agent cannot produce a valid output within its limits.

    Typical causes: the LLM provider keeps failing after its retries, the agent
    reaches its turn limit, or it gives too many invalid final answers.
    """
