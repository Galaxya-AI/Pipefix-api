"""Tests of the application log lines and of the LLM key errors."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
import requests

from journal.run_journal import RunJournal
from llm.errors import LLMError, LLMTransientError
from llm.http_client import post_json
from llm.providers.openai.provider import OpenAIProvider


def send_event(journal: RunJournal) -> None:
    journal.log("triage", "llm_error", {"error": "boom"})


class Wrapper:
    """Like AgentRunner: a log() method that forwards to the journal."""

    def __init__(self, journal: RunJournal) -> None:
        self.journal = journal

    def log(self, actor: str, event: str, data: dict[str, Any]) -> None:
        self.journal.log(actor, event, data)

    def work(self) -> None:
        self.log("workflow", "done", {"status": "triage_failed"})


def test_log_line_shows_the_real_module_function_and_line(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    journal = RunJournal(tmp_path, run_id="r1")
    with caplog.at_level(logging.INFO, logger="pipefix"):
        send_event(journal)
        Wrapper(journal).work()
        journal.log("workflow", "start", {"project": "g/app"})
    first, second, third = caplog.records
    assert (first.module, first.funcName, first.levelno) == ("test_readable_logs", "send_event", logging.ERROR)
    assert (second.funcName, second.levelno) == ("work", logging.ERROR)
    assert third.levelno == logging.INFO
    assert "(run r1)" in first.getMessage()


def test_api_key_is_never_in_the_error() -> None:
    class Session:
        def post(self, *args: Any, **kwargs: Any) -> Any:
            raise requests.exceptions.InvalidHeader("Invalid header value 'Bearer sk-secret\\n'")

    with pytest.raises(LLMError) as error:
        post_json(Session(), "https://api.example/v1", {}, {})  # type: ignore[arg-type]
    assert not isinstance(error.value, LLMTransientError)
    assert "sk-secret" not in str(error.value)
    assert error.value.__cause__ is None
    assert "API key" in str(error.value)


def test_key_with_spaces_or_line_break_is_cleaned() -> None:
    assert OpenAIProvider("openai", "  sk-abc\n").headers()["Authorization"] == "Bearer sk-abc"
