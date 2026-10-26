"""Builders of conversation messages, in the internal format of llm.base."""

from __future__ import annotations

from llm.base import Message


def system_message(content: str) -> Message:
    return {"role": "system", "content": content}


def user_message(content: str) -> Message:
    return {"role": "user", "content": content}


def tool_result_message(call_id: str, tool_name: str, content: str) -> Message:
    return {"role": "tool", "tool_call_id": call_id, "name": tool_name, "content": content}


def finish_reminder(finish_tool_name: str) -> Message:
    """Reminder sent when the LLM answers with text instead of calling its finish tool."""
    return user_message(f"Finish by calling {finish_tool_name}.")
