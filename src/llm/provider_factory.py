"""Provider factory: one entry point that sends each call to the right provider adapter.

Each agent declares its provider and model in .env (e.g. DIAGNOSIS_LLM_PROVIDER,
DIAGNOSIS_LLM_MODEL). ProviderClient receives the adapters at start-up and, for
each call, picks the adapter whose name matches the agent's provider.

Adding a provider = writing its adapter (a subclass of LLMProvider) and
passing it to ProviderClient; nothing else changes.
"""

from __future__ import annotations

from collections.abc import Iterable

from llm.base import LLMProvider, LLMResponse, Message, ToolDefinition
from llm.errors import LLMError


class ProviderClient:
    """Send each LLM call to the adapter of the requested provider.

    Parameters
    ----------
    providers : Iterable[LLMProvider]
        Adapters available at run time, one per provider name.

    Raises
    ------
    ValueError
        If two adapters have the same name, or an adapter has no name.

    Examples
    --------
    >>> client = ProviderClient([OpenAICompatProvider(...), AnthropicProvider(...)])
    >>> client.chat("anthropic", "claude-sonnet-...", messages, tools)
    """

    def __init__(self, providers: Iterable[LLMProvider]) -> None:
        # Adapters by provider name
        self.providers: dict[str, LLMProvider] = {}
        # Current adapter
        provider: LLMProvider
        # Register every adapter under its name
        for provider in providers:
            # An adapter without a name cannot be selected
            if not provider.name:
                # Configuration error
                raise ValueError(f"{type(provider).__name__} has no provider name.")
            # Two adapters with the same name would hide each other
            if provider.name in self.providers:
                # Configuration error
                raise ValueError(f"Two adapters are named '{provider.name}'.")
            # Register the adapter
            self.providers[provider.name] = provider

    def names(self) -> list[str]:
        """Return the names of the registered providers, sorted.

        Returns
        -------
        list[str]
            Provider names, used to check the .env values at start-up.
        """
        # Sorted names
        return sorted(self.providers)

    def chat(
        self,
        # Registered provider name
        provider: str,
        # Model name for this provider
        model: str,
        # Conversation in the internal format
        messages: list[Message],
        # Tools the LLM may call
        tools: list[ToolDefinition],
    ) -> LLMResponse:
        """Send the call to the adapter of the requested provider.

        Parameters
        ----------
        provider : str
            Registered provider name.
        model : str
            Model name for this provider.
        messages : list[Message]
            Conversation in the internal format.
        tools : list[ToolDefinition]
            Tools the LLM may call.

        Returns
        -------
        LLMResponse
            The normalised answer.

        Raises
        ------
        LLMError
            If no adapter is registered under this provider name.
        """
        # Adapter of the requested provider, if registered
        adapter: LLMProvider | None = self.providers.get(provider)
        # Unknown provider: final configuration error, never retried
        if adapter is None:
            # Error listing the valid names
            raise LLMError(f"Unknown LLM provider '{provider}'. Available: {self.names()}")
        # Call the provider's API
        return adapter.chat(model, messages, tools)
