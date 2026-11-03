"""Provider factory: one entry point that sends each call to the right provider adapter.

Each agent declares its provider and model in .env (e.g. DIAGNOSIS_LLM_PROVIDER,
DIAGNOSIS_LLM_MODEL). Adding a provider means writing its LLMProvider adapter
and adding it in build_providers.
"""

from __future__ import annotations

import os
from collections.abc import Iterable

from llm.base import LLMClient, LLMProvider, LLMResponse, Message, ToolDefinition
from llm.errors import LLMError
from llm.providers.openai.provider import OpenAIProvider, parse_reasoning_efforts
from llm.retry import RetryingLLMClient

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
# Local OpenAI-format server (provider "ollama"), e.g. http://localhost:11434/v1
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "")
LLM_TIMEOUT_S = int(os.getenv("LLM_TIMEOUT_S", "180"))
# Optional reasoning effort per OpenAI model, e.g. "gpt-6.1-terra=low,gpt-6-luna=none" (lower effort, lower cost)
OPENAI_REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "")


class ProviderClient:
    """Send each LLM call to the adapter registered under the requested provider name."""

    def __init__(self, providers: Iterable[LLMProvider]) -> None:
        self.providers: dict[str, LLMProvider] = {}
        for provider in providers:
            if not provider.name:
                raise ValueError(f"{type(provider).__name__} has no provider name.")
            if provider.name in self.providers:
                raise ValueError(f"Two adapters are named '{provider.name}'.")
            self.providers[provider.name] = provider

    def names(self) -> list[str]:
        return sorted(self.providers)

    def chat(self, provider: str, model: str, messages: list[Message], tools: list[ToolDefinition]) -> LLMResponse:
        adapter = self.providers.get(provider)
        # A configuration error: final, never retried
        if adapter is None:
            raise LLMError(f"Unknown LLM provider '{provider}'. Available: {self.names()}")
        return adapter.chat(model, messages, tools)


def build_providers(
    openai_api_key: str = OPENAI_API_KEY,
    ollama_base_url: str = OLLAMA_BASE_URL,
    timeout_s: int = LLM_TIMEOUT_S,
) -> list[LLMProvider]:
    """Create one adapter per provider whose key (or URL) is set; the list may be empty."""
    providers: list[LLMProvider] = []
    if openai_api_key:
        efforts = parse_reasoning_efforts(OPENAI_REASONING_EFFORT)
        providers.append(OpenAIProvider("openai", openai_api_key, timeout_s=timeout_s, reasoning_efforts=efforts))
    if ollama_base_url:
        providers.append(OpenAIProvider("ollama", "", base_url=ollama_base_url, timeout_s=timeout_s))
    return providers


def build_llm_client(providers: Iterable[LLMProvider] | None = None) -> LLMClient:
    """Provider routing plus retries; None uses the providers configured in .env.

    Raise LLMError if no provider is configured.
    """
    adapters = list(providers) if providers is not None else build_providers()
    if not adapters:
        raise LLMError("No LLM provider configured: set OPENAI_API_KEY or OLLAMA_BASE_URL.")
    return RetryingLLMClient(ProviderClient(adapters))
