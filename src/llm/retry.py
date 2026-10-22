"""LLM client with retries: repeats a failed call with a growing wait.

Network errors, rate limits and overloads are often temporary. This client
wraps another client and retries only temporary errors (LLMTransientError);
final errors (LLMError) and programming errors are never retried.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from llm.base import LLMClient, LLMResponse, LLMTransientError, Message, ToolDefinition


class RetryingLLMClient:
    """Wrap an LLM client and retry its temporary failures.

    Parameters
    ----------
    client : LLMClient
        Client that calls the providers (usually a ProviderClient).
    max_attempts : int, default 3
        Total number of attempts for one call (LLM_MAX_ATTEMPTS).
    base_delay : float, default 2.0
        Wait before the second attempt, in seconds; doubled at each new attempt.
    sleep : Callable[[float], None], default time.sleep
        Wait function, replaced by a no-op in tests.
    """

    def __init__(
        self,
        # Client that calls the providers
        client: LLMClient,
        # Total number of attempts for one call
        max_attempts: int = 3,
        # Wait before the second attempt, in seconds
        base_delay: float = 2.0,
        # Wait function, replaced by a no-op in tests
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        # Wrapped client
        self.client: LLMClient = client
        # Attempt limit
        self.max_attempts: int = max_attempts
        # First wait
        self.base_delay: float = base_delay
        # Wait function
        self.sleep: Callable[[float], None] = sleep

    def chat(
        self,
        # Registered provider name
        provider: str,
        # Model name for this provider
        model: str,
        # Conversation so far
        messages: list[Message],
        # Tools the LLM may call
        tools: list[ToolDefinition],
    ) -> LLMResponse:
        """Call the wrapped client, retrying temporary errors with a growing wait.

        Parameters
        ----------
        provider : str
            Registered provider name.
        model : str
            Model name for this provider.
        messages : list[Message]
            Conversation so far.
        tools : list[ToolDefinition]
            Tools the LLM may call.

        Returns
        -------
        LLMResponse
            The LLM's answer.

        Raises
        ------
        LLMTransientError
            The last temporary error, if every attempt fails.
        LLMError
            A final error, raised at once without any new attempt.
        """
        # Current attempt number
        attempt: int
        # Try up to max_attempts times
        for attempt in range(1, self.max_attempts + 1):
            # Call the wrapped client
            try:
                # Successful call: return the answer
                return self.client.chat(provider, model, messages, tools)
            # Temporary error: maybe a new attempt will succeed
            except LLMTransientError as error:
                # Last attempt failed: let the error go up
                if attempt == self.max_attempts:
                    # Re-raise the last temporary error
                    raise
                # Wait advised by the API, otherwise base_delay doubled at each attempt
                wait_s: float = error.retry_after or self.base_delay * 2 ** (attempt - 1)
                # Wait before the next attempt
                self.sleep(wait_s)
        # Unreachable: the loop either returns or raises
        raise LLMTransientError("No attempt was made: max_attempts must be at least 1.")
