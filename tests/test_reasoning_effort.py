"""Offline tests: reasoning models that refuse tools on /chat/completions; the provider's refusal on the page."""

from __future__ import annotations

from typing import Any

import pytest

import llm.providers.openai.provider as provider_module
from llm.errors import LLMError
from llm.providers.openai.provider import OpenAIProvider, parse_reasoning_efforts
from web_support import agent_failure_reason

REFUSAL = (
    'LLM API error 400: {\n "error": {\n "message": "Function tools with reasoning_effort are not supported '
    "for gpt-6-luna in /v1/chat/completions. To use function tools, use /v1/responses or set reasoning_effort "
    'to \'none\'.",\n "type": "invalid_request_error"'
)
ANSWER = {"choices": [{"message": {"content": "ok"}}], "usage": {}}
TOOLS = [{"name": "submit_triage", "description": "d", "parameters": {"type": "object", "properties": {}}}]


def fake_api(monkeypatch: pytest.MonkeyPatch, refuse_without_none: bool) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []

    def post_json(session: Any, url: str, headers: Any, payload: dict[str, Any], timeout_s: int) -> dict[str, Any]:
        payloads.append(dict(payload))
        if refuse_without_none and payload.get("reasoning_effort") != "none":
            raise LLMError(REFUSAL)
        return ANSWER

    monkeypatch.setattr(provider_module, "post_json", post_json)
    return payloads


def test_a_model_that_refuses_tools_with_reasoning_is_called_again_without_it(monkeypatch: pytest.MonkeyPatch) -> None:
    payloads = fake_api(monkeypatch, refuse_without_none=True)
    provider = OpenAIProvider("openai", "key")
    messages = [{"role": "user", "content": "hi"}]
    assert provider.chat("gpt-6-luna", messages, TOOLS).content == "ok"
    assert [p.get("reasoning_effort") for p in payloads] == [None, "none"]
    # Remembered: the next call goes right away with reasoning off
    provider.chat("gpt-6-luna", messages, TOOLS)
    assert payloads[-1]["reasoning_effort"] == "none" and len(payloads) == 3


def test_other_errors_are_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    def post_json(*args: Any) -> dict[str, Any]:
        raise LLMError("LLM API error 401: invalid key")

    monkeypatch.setattr(provider_module, "post_json", post_json)
    with pytest.raises(LLMError, match="401"):
        OpenAIProvider("openai", "key").chat("gpt-4o", [], TOOLS)


def test_reasoning_effort_from_the_settings_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    payloads = fake_api(monkeypatch, refuse_without_none=False)
    efforts = parse_reasoning_efforts("gpt-6.1-terra=low, bad-item ,gpt-6-luna=none")
    assert efforts == {"gpt-6.1-terra": "low", "gpt-6-luna": "none"}
    provider = OpenAIProvider("openai", "key", reasoning_efforts=efforts)
    provider.chat("gpt-6.1-terra", [], TOOLS)
    provider.chat("gpt-4o", [], TOOLS)
    assert payloads[0]["reasoning_effort"] == "low"
    assert "reasoning_effort" not in payloads[1]


def test_the_page_shows_why_the_provider_refused_the_request() -> None:
    reason = agent_failure_reason(f"LLM call failed for agent 'triage': {REFUSAL}")
    assert reason is not None
    assert reason.startswith("The LLM provider refused the request (HTTP 400): Function tools with reasoning_effort")
    network = agent_failure_reason("LLM call failed for agent 'triage': Network error towards x")
    assert network == "The LLM provider did not answer (network, API key or quota). Try again later."
