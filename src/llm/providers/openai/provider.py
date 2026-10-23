"""Provider adapter for the OpenAI Chat Completions API.

Also used by every server that copies the OpenAI format: Mistral, Groq,
Ollama, vLLM...

Each instance gets its own name, URL and key. The format conversions live in converters.py.
"""

from __future__ import annotations

from typing import Any

import requests
from llm.base import LLMProvider, LLMResponse, Message, ToolDefinition
from llm.http_client import post_json
from llm.providers.openai.converters import (
    parse_openai_response,
    to_openai_messages,
    to_openai_tools,
)


class OpenAIProvider(LLMProvider):
    """Provider adapter for the OpenAI Chat Completions API (and OpenAI-format servers).

    Parameters
    ----------
    name : str
        Provider name used in .env (e.g. "openai", "mistral", "ollama").
    api_key : str
        API key (may be empty for a local server).
    base_url : str, default "https://api.openai.com/v1"
        Base URL of the API; change it for another OpenAI-format server (e.g. Ollama).
    include_tool_name : bool, default False
        Add the tool name to tool results (required by Mistral).
    timeout_s : int, default 180
        Maximum duration of one request, in seconds.

    Examples
    --------
    >>> openai = OpenAIProvider("openai", api_key="...")
    >>> ollama = OpenAIProvider("ollama", api_key="", base_url="http://localhost:11434/v1")
    """

    def __init__(
        self,
        # Provider name used in .env
        name: str,
        # API key (may be empty for a local server)
        api_key: str,
        # Base URL of the API
        base_url: str = "https://api.openai.com/v1",
        # Add the tool name to tool results (Mistral)
        include_tool_name: bool = False,
        # Maximum duration of one request, in seconds
        timeout_s: int = 180,
    ) -> None:
        # Provider name, stored by the base class
        super().__init__(name)
        # API key, kept private
        self._api_key: str = api_key
        # URL without trailing "/"
        self._base_url: str = base_url.rstrip("/")
        # Mistral-specific option
        self._include_tool_name: bool = include_tool_name
        # Maximum duration of one request
        self._timeout_s: int = timeout_s
        # HTTP session reused between calls (faster)
        self._session: requests.Session = requests.Session()

    def chat(
        self, model: str, messages: list[Message], tools: list[ToolDefinition]
    ) -> LLMResponse:
        """Send the conversation to the Chat Completions API and return the normalised answer.

        Parameters
        ----------
        model : str
            Model name for this provider.
        messages : list[Message]
            Conversation in the internal format, starting with the system message.
        tools : list[ToolDefinition]
            Tools the LLM may call.

        Returns
        -------
        LLMResponse
            The normalised answer.

        Raises
        ------
        LLMTransientError
            If the error is temporary (raised by post_json).
        LLMError
            If the error is final or the answer is unusable.
        """
        # Request body: model and converted conversation
        payload: dict[str, Any] = {
            "model": model,
            "messages": to_openai_messages(messages, self._include_tool_name),
        }
        # Tools are available
        if tools:
            # Tools in the API format
            payload["tools"] = to_openai_tools(tools)
        # JSON header
        headers: dict[str, str] = {"Content-Type": "application/json"}
        # A key is provided (not the case for a local server)
        if self._api_key:
            # Bearer authentication
            headers["Authorization"] = f"Bearer {self._api_key}"
        # Call the API
        data: dict[str, Any] = post_json(
            self._session,
            f"{self._base_url}/chat/completions",
            headers,
            payload,
            self._timeout_s,
        )
        # Convert the answer to the internal format
        return parse_openai_response(data)
