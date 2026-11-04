"""Space the LLM calls of the whole pod: at least LLM_MIN_INTERVAL_S between two calls.

Several runs at once would otherwise send their calls in bursts, and the provider
answers "too many requests" (HTTP 429). The wait is shared by every thread, so the
calls go one after the other; a single run only waits a few seconds in all.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from llm.base import LLMClient, LLMResponse, Message, ToolDefinition
from startup.env_values import env_float

LLM_MIN_INTERVAL_S = env_float("LLM_MIN_INTERVAL_S", 0.5)


class CallSpacing:
    """Gives each caller its turn, at least interval_s after the previous one."""

    def __init__(
        self,
        interval_s: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.interval_s = max(0.0, interval_s)
        self.clock = clock
        self.sleep = sleep
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait_turn(self) -> None:
        if self.interval_s <= 0:
            return
        with self.lock:
            now = self.clock()
            turn = max(now, self.next_at)
            self.next_at = turn + self.interval_s
        if turn > now:
            self.sleep(turn - now)


CALL_SPACING = CallSpacing(LLM_MIN_INTERVAL_S)


class SpacedLLMClient:
    """Wrap an LLM client so that every call, retries included, waits for its turn."""

    def __init__(self, client: LLMClient, spacing: CallSpacing = CALL_SPACING) -> None:
        self.client = client
        self.spacing = spacing

    def chat(self, provider: str, model: str, messages: list[Message], tools: list[ToolDefinition]) -> LLMResponse:
        self.spacing.wait_turn()
        return self.client.chat(provider, model, messages, tools)
