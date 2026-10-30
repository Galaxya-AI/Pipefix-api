"""Provider adapter for the OpenAI Chat Completions API.

Also used by every server that copies the OpenAI format: Groq, Ollama,
vLLM... (Mistral has its own small subclass). Format conversions live in converters.py.
"""

from __future__ import annotations

from typing import Any

import requests

from llm.base import LLMProvider, LLMResponse, Message, ToolDefinition
from llm.http_client import post_json
from llm.providers.openai.converters import parse_openai_response, to_openai_messages, to_openai_tools

OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAIProvider(LLMProvider):
    """`api_key` may be empty for a local server; `include_tool_name` is required by Mistral."""

    def __init__(
        self,
        name: str,
        api_key: str,
        base_url: str = OPENAI_DEFAULT_BASE_URL,
        include_tool_name: bool = False,
        timeout_s: int = 180,
    ) -> None:
        super().__init__(name)
        # A key copied with a space or a line break around it would make every request fail.
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.include_tool_name = include_tool_name
        self.timeout_s = timeout_s
        self.session = requests.Session()

    def headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def chat(self, model: str, messages: list[Message], tools: list[ToolDefinition]) -> LLMResponse:
        payload: dict[str, Any] = {"model": model, "messages": to_openai_messages(messages, self.include_tool_name)}
        if tools:
            payload["tools"] = to_openai_tools(tools)
        data = post_json(self.session, f"{self.base_url}/chat/completions", self.headers(), payload, self.timeout_s)
        return parse_openai_response(data)
