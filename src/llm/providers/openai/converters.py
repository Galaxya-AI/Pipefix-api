"""Conversions between the internal format and the OpenAI Chat Completions format.

The Chat Completions format is used by OpenAI, Mistral, Groq and most local
servers (Ollama, vLLM, LM Studio). These functions only transform data; the
HTTP call is made by OpenAIProvider.

Each function converts one small piece:
- requests: to_openai_tools, to_openai_messages -> to_openai_message ->
  to_openai_text_message / to_openai_assistant_message (-> to_openai_tool_call) /
  to_openai_tool_result;
- answers: parse_openai_response -> first_openai_message, extract_openai_text,
  parse_openai_tool_call (-> decode_tool_arguments).
"""

from __future__ import annotations

import json
from typing import Any

from llm.base import LLMResponse, Message, ToolCall, ToolDefinition
from llm.errors import LLMError


def to_openai_tools(tools: list[ToolDefinition]) -> list[dict[str, Any]]:
    """Wrap each tool definition in the "function" format of the API.

    Parameters
    ----------
    tools : list[ToolDefinition]
        Tools in the internal format (name, description, parameters).

    Returns
    -------
    list[dict[str, Any]]
        Tools in the Chat Completions format.
    """
    # One "function" entry per tool
    return [{"type": "function", "function": tool} for tool in tools]


def to_openai_messages(messages: list[Message], include_tool_name: bool = False) -> list[dict[str, Any]]:
    """Convert the internal conversation to the Chat Completions format.

    Parameters
    ----------
    messages : list[Message]
        Conversation in the internal format.
    include_tool_name : bool, default False
        Add the tool name to tool results (required by Mistral).

    Returns
    -------
    list[dict[str, Any]]
        Messages in the format expected by the API.
    """
    # Each message converted on its own, in order
    return [to_openai_message(message, include_tool_name) for message in messages]


def to_openai_message(message: Message, include_tool_name: bool = False) -> dict[str, Any]:
    """Convert one message, according to its role.

    Parameters
    ----------
    message : Message
        Message in the internal format.
    include_tool_name : bool, default False
        Add the tool name to tool results (required by Mistral).

    Returns
    -------
    dict[str, Any]
        Message in the API format.

    Raises
    ------
    LLMError
        If the role is unknown.
    """
    # Role of the message
    role: str = message["role"]
    # System or user message
    if role in ("system", "user"):
        # Same format in both
        return to_openai_text_message(message)
    # Answer of the LLM
    if role == "assistant":
        # Text and tool calls
        return to_openai_assistant_message(message)
    # Result of a tool
    if role == "tool":
        # Result linked to its call
        return to_openai_tool_result(message, include_tool_name)
    # Unknown role: programming error in the conversation
    raise LLMError(f"Unknown message role: {role}")


def to_openai_text_message(message: Message) -> dict[str, Any]:
    """Convert a system or user message (role and text only).

    Parameters
    ----------
    message : Message
        System or user message in the internal format.

    Returns
    -------
    dict[str, Any]
        The same message in the API format.
    """
    # Role and text copied as-is
    return {"role": message["role"], "content": message["content"]}


def to_openai_assistant_message(message: Message) -> dict[str, Any]:
    """Convert an assistant message, with its tool calls.

    Parameters
    ----------
    message : Message
        Assistant message in the internal format.

    Returns
    -------
    dict[str, Any]
        Assistant message in the API format.
    """
    # Tool calls of the message, if any
    calls: list[dict[str, Any]] = message.get("tool_calls") or []
    # Text of the message; null is allowed only when there are tool calls
    content: str | None = message.get("content") or (None if calls else "")
    # Assistant message with its text
    converted: dict[str, Any] = {"role": "assistant", "content": content}
    # Tools were requested
    if calls:
        # Each call in the API format
        converted["tool_calls"] = [to_openai_tool_call(call) for call in calls]
    # Converted message
    return converted


def to_openai_tool_call(call: dict[str, Any]) -> dict[str, Any]:
    """Convert one tool call; the API expects the arguments as JSON text.

    Parameters
    ----------
    call : dict[str, Any]
        Tool call in the internal format (id, name, arguments).

    Returns
    -------
    dict[str, Any]
        Tool call in the API format.
    """
    # Call with its arguments encoded as JSON text
    return {
        "id": call["id"],
        "type": "function",
        "function": {"name": call["name"], "arguments": json.dumps(call["arguments"], ensure_ascii=False)},
    }


def to_openai_tool_result(message: Message, include_tool_name: bool = False) -> dict[str, Any]:
    """Convert the result of a tool call.

    Parameters
    ----------
    message : Message
        Tool message in the internal format.
    include_tool_name : bool, default False
        Add the tool name (required by Mistral).

    Returns
    -------
    dict[str, Any]
        Tool result in the API format.
    """
    # Result linked to its call
    result: dict[str, Any] = {"role": "tool", "tool_call_id": message["tool_call_id"], "content": message["content"]}
    # Mistral requires the tool name
    if include_tool_name:
        # Add the name
        result["name"] = message["name"]
    # Converted result
    return result


def parse_openai_response(data: dict[str, Any]) -> LLMResponse:
    """Convert a Chat Completions answer into an LLMResponse.

    Parameters
    ----------
    data : dict[str, Any]
        Raw JSON answer of the API.

    Returns
    -------
    LLMResponse
        The normalised answer.

    Raises
    ------
    LLMError
        If the answer contains no choice.
    """
    # Message of the first choice
    message: dict[str, Any] = first_openai_message(data)
    # Token usage reported by the API
    usage: dict[str, Any] = data.get("usage") or {}
    # Normalised answer
    return LLMResponse(
        content=extract_openai_text(message),
        tool_calls=tuple(parse_openai_tool_call(raw_call) for raw_call in message.get("tool_calls") or []),
        input_tokens=int(usage.get("prompt_tokens", 0) or 0),
        output_tokens=int(usage.get("completion_tokens", 0) or 0),
    )


def first_openai_message(data: dict[str, Any]) -> dict[str, Any]:
    """Return the message of the first choice of the answer.

    Parameters
    ----------
    data : dict[str, Any]
        Raw JSON answer of the API.

    Returns
    -------
    dict[str, Any]
        Message of the first choice (empty if missing).

    Raises
    ------
    LLMError
        If the answer contains no choice.
    """
    # Choices proposed by the API
    choices: list[dict[str, Any]] = data.get("choices") or []
    # No choice: unusable answer
    if not choices:
        # Final error with an excerpt of the answer
        raise LLMError(f'Answer without "choices": {str(data)[:300]}')
    # Message of the first choice
    message: dict[str, Any] = choices[0].get("message") or {}
    # Message found
    return message


def extract_openai_text(message: dict[str, Any]) -> str:
    """Return the text of an answer message, joining chunks when the server splits it.

    Parameters
    ----------
    message : dict[str, Any]
        Message of the answer.

    Returns
    -------
    str
        Text of the message (empty if none).
    """
    # Text, or a list of chunks on some servers
    content: Any = message.get("content") or ""
    # Content split into chunks
    if isinstance(content, list):
        # Chunks joined back into one text
        return "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    # Plain text
    return str(content)


def parse_openai_tool_call(raw_call: dict[str, Any]) -> ToolCall:
    """Convert one tool call of the answer into a ToolCall.

    Parameters
    ----------
    raw_call : dict[str, Any]
        Tool call as sent by the API.

    Returns
    -------
    ToolCall
        Normalised tool call.
    """
    # "function" part of the call
    function: dict[str, Any] = raw_call.get("function") or {}
    # Normalised call with decoded arguments
    return ToolCall(
        id=str(raw_call.get("id", "")),
        name=str(function.get("name", "")),
        arguments=decode_tool_arguments(function.get("arguments")),
    )


def decode_tool_arguments(raw_arguments: Any) -> dict[str, Any]:
    """Decode the arguments of a tool call, usually sent as JSON text.

    Parameters
    ----------
    raw_arguments : Any
        Arguments as sent by the API (JSON text, or already a dictionary on some servers).

    Returns
    -------
    dict[str, Any]
        Decoded arguments; unreadable arguments are kept under the "_invalid_json" key so the tool can report it.
    """
    # Key under which unreadable arguments are kept, so the tool can report the error
    invalid_json_key: str = "_invalid_json"
    # Already a dictionary (some servers)
    if isinstance(raw_arguments, dict):
        # Kept as-is
        return raw_arguments
    # Decode the JSON text
    try:
        # Decoded value
        decoded: Any = json.loads(raw_arguments or "{}")
    # Invalid JSON: the tool will report the error to the LLM
    except json.JSONDecodeError:
        # Raw text kept under the special key
        return {invalid_json_key: str(raw_arguments)}
    # A JSON object is expected; anything else is reported the same way
    return decoded if isinstance(decoded, dict) else {invalid_json_key: str(raw_arguments)}
