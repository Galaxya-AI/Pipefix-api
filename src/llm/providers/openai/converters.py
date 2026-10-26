"""Conversions between the internal format and the OpenAI Chat Completions format.

The Chat Completions format is used by OpenAI, Mistral, Groq and most local
servers (Ollama, vLLM, LM Studio).
"""

from __future__ import annotations

import json
from typing import Any

from llm.base import LLMResponse, Message, ToolCall, ToolDefinition
from llm.errors import LLMError


def to_openai_tools(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    return [{"type": "function", "function": tool} for tool in tools]


def to_openai_messages(messages: list[Message], include_tool_name: bool = False) -> list[dict[str, Any]]:
    return [to_openai_message(message, include_tool_name) for message in messages]


def to_openai_message(message: Message, include_tool_name: bool = False) -> dict[str, Any]:
    role = message["role"]
    if role in ("system", "user"):
        return to_openai_text_message(message)
    if role == "assistant":
        return to_openai_assistant_message(message)
    if role == "tool":
        return to_openai_tool_result(message, include_tool_name)
    raise LLMError(f"Unknown message role: {role}")


def to_openai_text_message(message: Message) -> dict[str, Any]:
    return {"role": message["role"], "content": message["content"]}


def to_openai_assistant_message(message: Message) -> dict[str, Any]:
    calls: list[dict[str, Any]] = message.get("tool_calls") or []
    # The API allows a null content only when there are tool calls
    content: str | None = message.get("content") or (None if calls else "")
    converted: dict[str, Any] = {"role": "assistant", "content": content}
    if calls:
        converted["tool_calls"] = [to_openai_tool_call(call) for call in calls]
    return converted


def to_openai_tool_call(call: dict[str, Any]) -> dict[str, Any]:
    # The API expects the arguments as JSON text
    return {
        "id": call["id"],
        "type": "function",
        "function": {"name": call["name"], "arguments": json.dumps(call["arguments"], ensure_ascii=False)},
    }


def to_openai_tool_result(message: Message, include_tool_name: bool = False) -> dict[str, Any]:
    result = {"role": "tool", "tool_call_id": message["tool_call_id"], "content": message["content"]}
    # Required by Mistral
    if include_tool_name:
        result["name"] = message["name"]
    return result


def parse_openai_response(data: dict[str, Any]) -> LLMResponse:
    """Raise LLMError if the answer contains no choice."""
    message = first_openai_message(data)
    usage: dict[str, Any] = data.get("usage") or {}
    return LLMResponse(
        content=extract_openai_text(message),
        tool_calls=tuple(parse_openai_tool_call(raw_call) for raw_call in message.get("tool_calls") or []),
        input_tokens=int(usage.get("prompt_tokens", 0) or 0),
        output_tokens=int(usage.get("completion_tokens", 0) or 0),
    )


def first_openai_message(data: dict[str, Any]) -> dict[str, Any]:
    """Return the message of the first choice (empty if missing); raise LLMError if there is no choice."""
    choices: list[dict[str, Any]] = data.get("choices") or []
    if not choices:
        raise LLMError(f'Answer without "choices": {str(data)[:300]}')
    message: dict[str, Any] = choices[0].get("message") or {}
    return message


def extract_openai_text(message: dict[str, Any]) -> str:
    content = message.get("content") or ""
    # Some servers split the text into a list of chunks
    if isinstance(content, list):
        return "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return str(content)


def parse_openai_tool_call(raw_call: dict[str, Any]) -> ToolCall:
    function: dict[str, Any] = raw_call.get("function") or {}
    return ToolCall(
        id=str(raw_call.get("id", "")),
        name=str(function.get("name", "")),
        arguments=decode_tool_arguments(function.get("arguments")),
    )


def decode_tool_arguments(raw_arguments: Any) -> dict[str, Any]:
    """Decode arguments sent as JSON text (or already a dictionary on some servers).

    Unreadable arguments are kept under the "_invalid_json" key so the tool can report the error to the LLM.
    """
    invalid_json_key = "_invalid_json"
    if isinstance(raw_arguments, dict):
        return raw_arguments
    try:
        decoded = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError:
        return {invalid_json_key: str(raw_arguments)}
    return decoded if isinstance(decoded, dict) else {invalid_json_key: str(raw_arguments)}
