"""Read numbers and switches from the environment.

An empty value (e.g. "MAX_ROUNDS=" copied from .env.example) means "use the
default", instead of stopping the application at start-up with int("").
"""

from __future__ import annotations

import os

TRUE_VALUES = ("1", "true", "yes", "on")


def env_int(name: str, default: int, minimum: int | None = None) -> int:
    """Return the integer value of `name`, or `default` when it is missing, empty or not a number."""
    raw = os.getenv(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def env_bool(name: str, default: bool) -> bool:
    """Return True for 1/true/yes/on, False for any other value, `default` when missing or empty."""
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in TRUE_VALUES
