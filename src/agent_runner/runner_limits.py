"""Limits of an agent run, read from the environment (.env).

Changing a limit does not require changing the code: set the variable in .env.
"""

# Lazy annotations
from __future__ import annotations

# Read-only mapping type for the environment variables
from collections.abc import Mapping

# Immutable data container
from dataclasses import dataclass


def _read_int(env: Mapping[str, str], name: str, default: int, minimum: int) -> int:
    """Read an integer variable, with a default value and a minimum.

    Parameters
    ----------
    env : Mapping[str, str]
        Environment variables.
    name : str
        Name of the variable.
    default : int
        Value used when the variable is missing or empty.
    minimum : int
        Smallest accepted value.

    Returns
    -------
    int
        The value of the variable.

    Raises
    ------
    ValueError
        If the value is not an integer or is below the minimum.
    """
    # Raw text of the variable, without spaces
    raw: str = env.get(name, "").strip()
    # Missing or empty: default value
    if not raw:
        # Default value
        return default
    # Convert the text to an integer
    try:
        # Integer value
        value: int = int(raw)
    # Not an integer: clear configuration error
    except ValueError as error:
        # Error with the variable name
        raise ValueError(f"{name} must be an integer, got '{raw}'.") from error
    # Below the minimum: clear configuration error
    if value < minimum:
        # Error with the variable name
        raise ValueError(f"{name} must be at least {minimum}, got {value}.")
    # Valid value
    return value


@dataclass(frozen=True)
class RunnerLimits:
    """Limits of one agent run.

    Parameters
    ----------
    max_turns : int, default 10
        Maximum number of LLM turns for one agent run (AGENT_MAX_TURNS).
    max_invalid_answers : int, default 2
        Invalid final answers accepted before the run is stopped (AGENT_MAX_INVALID_ANSWERS).
    max_sub_agent_depth : int, default 2
        Maximum depth of sub-agent calls (AGENT_MAX_SUB_AGENT_DEPTH).
    """

    # Turn limit per agent run
    max_turns: int = 10
    # Invalid-answer limit per agent run
    max_invalid_answers: int = 2
    # Depth limit of sub-agent calls
    max_sub_agent_depth: int = 2

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> RunnerLimits:
        """Build the limits from environment variables, with defaults.

        Parameters
        ----------
        env : Mapping[str, str]
            Environment variables (e.g. os.environ after loading .env).

        Returns
        -------
        RunnerLimits
            The limits.
        """
        # Read each limit with its default and minimum
        return cls(
            max_turns=_read_int(env, "AGENT_MAX_TURNS", 10, 1),
            max_invalid_answers=_read_int(env, "AGENT_MAX_INVALID_ANSWERS", 2, 0),
            max_sub_agent_depth=_read_int(env, "AGENT_MAX_SUB_AGENT_DEPTH", 2, 0),
        )
