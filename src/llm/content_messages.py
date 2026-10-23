"""Builders of the conversation messages sent to the LLM.

All messages use the internal format described in llm.base; each provider
adapter converts it to its own format. Changing the format only changes
this module and llm.base.
"""

from __future__ import annotations

from llm.base import Message


def system_message(content: str) -> Message:
    """Return the system message: the agent's permanent instructions.

    Parameters
    ----------
    content : str
        System prompt of the agent.

    Returns
    -------
    Message
        Message with the role "system".
    """
    # Role "system"
    return {"role": "system", "content": content}


def user_message(content: str) -> Message:
    """Return a user message: the task, or a reminder from the runner.

    Parameters
    ----------
    content : str
        Text of the message.

    Returns
    -------
    Message
        Message with the role "user".
    """
    # Role "user"
    return {"role": "user", "content": content}


def tool_result_message(call_id: str, tool_name: str, content: str) -> Message:
    """Return the result of a tool call, linked to the call by its id.

    Parameters
    ----------
    call_id : str
        Identifier of the tool call, given by the provider.
    tool_name : str
        Name of the called tool.
    content : str
        Result of the tool, as text.

    Returns
    -------
    Message
        Message with the role "tool".
    """
    # Role "tool", linked to the call
    return {"role": "tool", "tool_call_id": call_id, "name": tool_name, "content": content}


def finish_reminder(finish_tool_name: str) -> Message:
    """Return the reminder sent when the LLM answers with text instead of finishing.

    Parameters
    ----------
    finish_tool_name : str
        Name of the agent's finish tool (e.g. "submit_triage").

    Returns
    -------
    Message
        User message asking the LLM to call the finish tool.
    """
    # Ask for the finish tool explicitly
    return user_message(f"Finish by calling {finish_tool_name}.")
