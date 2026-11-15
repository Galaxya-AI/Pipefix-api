"""Offline tests: reasoning models that refuse tools on /chat/completions; the provider's refusal given to visitors."""

from __future__ import annotations

from typing import Any

import pytest

import llm.providers.openai.provider as provider_module
from llm.errors import LLMError
from llm.providers.openai.provider import OpenAIProvider, parse_reasoning_efforts
from visitor_api.views import agent_failure_reason

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


def test_visitors_learn_why_the_provider_refused_the_request() -> None:
    reason = agent_failure_reason(f"LLM call failed for agent 'triage': {REFUSAL}")
    assert reason is not None
    assert reason.startswith("The LLM provider refused the request (HTTP 400): Function tools with reasoning_effort")
    network = agent_failure_reason("LLM call failed for agent 'triage': Network error towards x")
    assert network == "The LLM provider did not answer (network, API key or quota). Try again later."


def test_cached_input_tokens_are_read_and_added_up() -> None:
    from llm.providers.openai.converters import parse_openai_response
    from llm.usage import UsageTracker

    answer = {
        "choices": [{"message": {"content": "ok"}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 50, "prompt_tokens_details": {"cached_tokens": 768}},
    }
    response = parse_openai_response(answer)
    assert response.cached_input_tokens == 768
    assert parse_openai_response({"choices": [{"message": {}}], "usage": {}}).cached_input_tokens == 0
    usage = UsageTracker()
    usage.add("fixer", response)
    usage.add("fixer", response)
    assert usage.as_dict()["fixer"]["cached_input_tokens"] == 1536
    assert usage.totals() == {"input_tokens": 2000, "cached_input_tokens": 1536, "output_tokens": 100}
