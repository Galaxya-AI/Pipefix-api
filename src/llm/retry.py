"""LLM client that retries temporary errors (LLMTransientError) with a growing wait."""

from __future__ import annotations

import os
import time
from collections.abc import Callable

from llm.base import LLMClient, LLMResponse, Message, ToolDefinition
from llm.errors import LLMTransientError

LLM_MAX_ATTEMPTS = int(os.getenv("LLM_MAX_ATTEMPTS", "3"))
# Wait before the second attempt, doubled at each new attempt
LLM_RETRY_BASE_DELAY_S = float(os.getenv("LLM_RETRY_BASE_DELAY_S", "2.0"))


class RetryingLLMClient:
    """Wrap an LLM client; final errors (LLMError) and programming errors are never retried."""

    def __init__(
        self,
        client: LLMClient,
        max_attempts: int = LLM_MAX_ATTEMPTS,
        base_delay_s: float = LLM_RETRY_BASE_DELAY_S,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.max_attempts = max(1, max_attempts)
        self.base_delay_s = base_delay_s
        self.sleep = sleep

    def chat(self, provider: str, model: str, messages: list[Message], tools: list[ToolDefinition]) -> LLMResponse:
        for attempt in range(1, self.max_attempts + 1):
            try:
                return self.client.chat(provider, model, messages, tools)
            except LLMTransientError as error:
                if attempt == self.max_attempts:
                    raise
                self.sleep(error.retry_after or self.base_delay_s * 2 ** (attempt - 1))
        raise LLMTransientError("No attempt was made.")
