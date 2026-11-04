"""Offline tests: fewer LLM calls per round (error lines, edits in one call, files given to the agents)."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest

from agent_runner.agent_runner import AgentRunner
from agents.diagnostic_agent import DiagnosisOutput
from agents.fixer_agent import FixerAgent
from agents.reviewer import ReviewerAgent
from changeset.changeset import Changeset
from llm.base import LLMResponse, Message, ToolCall, ToolDefinition
from text_tools.log_cleaning import error_summary
from tools.errors import ToolError
from tools.registry import ToolRegistry
from workflow_analysis.remediation_round import RemediationRound
from workflow_analysis.task_messages import file_contents, fix_task
from workflow_remediation.round_result import RoundResult

FINANCE = "def double(x):\n    return x * 3\n\n\ndef half(x):\n    return x / 3\n"


class ScriptedLLM:
    """Gives the prepared answers in order and keeps the messages of every call."""

    def __init__(self, answers: list[LLMResponse]) -> None:
        self.answers = list(answers)
        self.calls: list[list[Message]] = []

    def chat(self, provider: str, model: str, messages: list[Message], tools: list[ToolDefinition]) -> LLMResponse:
        self.calls.append(list(messages))
        return self.answers.pop(0)


def answer(*calls: tuple[str, dict[str, Any]]) -> LLMResponse:
    return LLMResponse(tool_calls=tuple(ToolCall(f"c{i}", name, args) for i, (name, args) in enumerate(calls)))


def edit(old: str, new: str) -> dict[str, str]:
    return {"path": "src/finance.py", "old_text": old, "new_text": new}


FIX_DONE = {"summary": "Fixed double and half.", "files_changed": ["src/finance.py"], "tests_modified": False}
REVIEW_OK = {"summary": "Correct.", "approved": True, "risk": "low", "comment": "Both functions fixed."}


def agents() -> dict[str, Any]:
    return {
        "fixer": FixerAgent(llm_provider="fake", llm_model="fake"),
        "reviewer": ReviewerAgent(llm_provider="fake", llm_model="fake"),
    }


def test_failed_test_names_are_kept_before_the_assertion_details() -> None:
    details = "\n".join(f"E         -     {i}," for i in range(100))
    log = f"{details}\n=== short test summary info ===\nFAILED tests/test_a.py::test_paginate - assert\n"
    log += "FAILED tests/test_a.py::test_stable_unique - AssertionError\n"
    lines = error_summary(log, 30)
    assert len(lines) == 30
    assert lines[:2] == [
        "FAILED tests/test_a.py::test_paginate - assert",
        "FAILED tests/test_a.py::test_stable_unique - AssertionError",
    ]


def test_propose_edits_applies_every_edit_or_none() -> None:
    changeset = Changeset(lambda path: FINANCE)
    diff = changeset.propose_edits([edit("x * 3", "x * 2"), edit("x / 3", "x / 2")])
    assert "+    return x * 2" in diff and "+    return x / 2" in diff
    assert changeset.current("src/finance.py") == FINANCE.replace("3", "2")

    fresh = Changeset(lambda path: FINANCE)
    with pytest.raises(ToolError, match="Edit 2 of 2"):
        fresh.propose_edits([edit("x * 3", "x * 2"), edit("not in the file", "y")])
    assert fresh.is_empty()


def test_fixer_proposes_and_finishes_in_one_llm_call() -> None:
    changeset = Changeset(lambda path: FINANCE)
    llm = ScriptedLLM([answer(("propose_edits", {"edits": [edit("x * 3", "x * 2")]}), ("submit_fixer", FIX_DONE))])
    runner = AgentRunner(llm, ToolRegistry(changeset.tools()), agents())
    output = runner.run("fixer", "task")
    assert output.summary == "Fixed double and half."
    assert runner.usage.total_calls() == 1
    assert not changeset.is_empty()


def test_finish_is_refused_when_an_edit_of_the_same_answer_failed() -> None:
    changeset = Changeset(lambda path: FINANCE)
    llm = ScriptedLLM(
        [
            answer(("propose_edits", {"edits": [edit("x * 4", "x * 2")]}), ("submit_fixer", FIX_DONE)),
            answer(("propose_edits", {"edits": [edit("x * 3", "x * 2")]}), ("submit_fixer", FIX_DONE)),
        ]
    )
    runner = AgentRunner(llm, ToolRegistry(changeset.tools()), agents())
    runner.run("fixer", "task")
    assert runner.usage.total_calls() == 2
    second_call = llm.calls[1]
    assert any("Not recorded: another call of this answer failed" in m.get("content", "") for m in second_call)
    assert changeset.current("src/finance.py").count("x * 2") == 1


def test_file_contents_reports_missing_files_and_cuts_long_ones() -> None:
    def read(path: str) -> str:
        if path == "missing.py":
            raise FileNotFoundError(path)
        return "a" * 50

    text = file_contents(read, ["src/x.py", "missing.py", "src/x.py"], max_chars=10, max_files=5)
    assert text.count("### src/x.py") == 1
    assert "cut after 10 characters" in text
    assert "### missing.py\n(could not be read: FileNotFoundError" in text


def test_round_gives_the_files_to_the_fixer_and_counts_the_calls_per_agent() -> None:
    changeset = Changeset(lambda path: FINANCE)
    llm = ScriptedLLM(
        [
            answer(("propose_edits", {"edits": [edit("x * 3", "x * 2")]}), ("submit_fixer", FIX_DONE)),
            answer(("submit_reviewer", REVIEW_OK)),
        ]
    )
    runner = AgentRunner(llm, ToolRegistry(changeset.tools()), agents())
    journal = Mock()
    remediation = RemediationRound(Mock(), runner, [], Mock(), journal, dry_run=True)
    result = RoundResult(pipeline_id=7)
    result.diagnosis = DiagnosisOutput(
        summary="Wrong factor.",
        root_cause="double multiplies by 3",
        files=["src/finance.py"],
        evidence="src/finance.py:2 return x * 3",
        fix_plan="Multiply by 2 instead of 3.",
        confidence="high",
    )
    before = remediation.llm_calls()
    remediation.fix_and_review("context", changeset, result)
    remediation.log_llm_calls({"id": 7}, before)

    assert result.status == "fix_ready"
    fixer_task = llm.calls[0][1]["content"]
    assert "## Current content of the files to change" in fixer_task and "return x * 3" in fixer_task
    reviewer_task = llm.calls[1][1]["content"]
    assert "## Changed files after the fix (full content)" in reviewer_task and "return x * 2" in reviewer_task
    calls = [c.args[2] for c in journal.log.call_args_list if c.args[:2] == ("remediation_round", "llm_calls")]
    assert calls and {key: calls[0][key] for key in ("pipeline", "per_agent", "round_calls", "run_calls")} == {
        "pipeline": 7,
        "per_agent": {"fixer": 1, "reviewer": 1},
        "round_calls": 2,
        "run_calls": 2,
    }
    assert calls[0]["run_tokens"] == {"input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0}


def test_fix_task_without_files_is_unchanged() -> None:
    diagnosis = Mock()
    diagnosis.model_dump.return_value = {"summary": "s"}
    assert "Current content" not in fix_task("ctx", diagnosis, [])


def round_with_jobs(jobs: list[dict[str, Any]]) -> tuple[RemediationRound, Mock, Mock]:
    platform = Mock()
    platform.pipeline_jobs.return_value = jobs
    platform.job_trace.return_value = "ERROR: Job failed (system failure)"
    runner = Mock()
    journal = Mock()
    return RemediationRound(platform, runner, [], Mock(), journal, dry_run=True), runner, journal


def test_infrastructure_failure_given_by_gitlab_needs_no_llm_call(monkeypatch: pytest.MonkeyPatch) -> None:
    import workflow_analysis.remediation_round as remediation_round

    monkeypatch.setattr(remediation_round, "build_run_registry", lambda *args: Mock())
    jobs = [
        {"id": 11, "name": "unit-tests", "failure_reason": "runner_system_failure"},
        {"id": 12, "name": "lint", "failure_reason": "stuck_or_timeout_failure"},
    ]
    remediation, runner, journal = round_with_jobs(jobs)
    result = remediation.run("group/project", {"id": 7, "sha": "abc"}, allow_research=False)

    assert result.status == "infra_flaky"
    assert result.triage is not None and result.triage.category == "infra_flaky"
    runner.run.assert_not_called()
    journal.log.assert_any_call("triage", "start", {"source": "gitlab_failure_reason"})


@pytest.mark.parametrize(
    "reasons",
    [["script_failure"], ["runner_system_failure", "script_failure"], [None]],
)
def test_other_failures_still_go_to_the_triage_agent(reasons: list[str | None]) -> None:
    from workflow_analysis.infra_failure import infra_triage

    jobs = [{"id": i + 1, "name": f"job{i}", "failure_reason": reason} for i, reason in enumerate(reasons)]
    assert infra_triage(jobs) is None
    assert infra_triage([]) is None


def test_propose_edits_refuses_an_edit_that_adds_a_python_error() -> None:
    from changeset.static_check import added_problems_with_lines

    source = "from decimal import Decimal\n\n\ndef total(x):\n    return Decimal(x)\n"
    changeset = Changeset(lambda path: source, check=added_problems_with_lines)
    change = {
        "path": "src/finance.py",
        "old_text": "Decimal(x)\n",
        "new_text": "Decimal(x).quantize(1, ROUND_HALF_UP)\n",
    }
    with pytest.raises(ToolError) as error:
        changeset.propose_edits([change])
    message = str(error.value)
    assert "undefined name 'ROUND_HALF_UP'" in message
    assert "5 |     return Decimal(x).quantize(1, ROUND_HALF_UP)" in message
    assert changeset.is_empty()

    fixed = {
        "path": "src/finance.py",
        "old_text": "from decimal import Decimal\n",
        "new_text": "from decimal import ROUND_HALF_UP, Decimal\n",
    }
    changeset.propose_edits([change, fixed])
    assert "ROUND_HALF_UP, Decimal" in changeset.current("src/finance.py")


def test_round_says_static_check_failed_when_the_reviewer_never_ran() -> None:
    broken = {"summary": "Broken.", "files_changed": ["src/finance.py"], "tests_modified": False}
    changeset = Changeset(lambda path: FINANCE)
    llm = ScriptedLLM(
        [
            answer(("propose_edit", edit("x * 3", "x * (")), ("submit_fixer", broken)),
            answer(("submit_fixer", broken)),
        ]
    )
    runner = AgentRunner(llm, ToolRegistry(changeset.tools()), agents())
    remediation = RemediationRound(Mock(), runner, [], Mock(), Mock(), dry_run=True, max_fix_attempts=2)
    result = RoundResult(pipeline_id=7)
    result.diagnosis = DiagnosisOutput(
        summary="Wrong factor.",
        root_cause="double multiplies by 3",
        files=["src/finance.py"],
        evidence="src/finance.py:2 return x * 3",
        fix_plan="Multiply by 2 instead of 3.",
        confidence="high",
    )
    remediation.fix_and_review("context", changeset, result)
    assert result.status == "static_check_failed"
    assert "syntax error" in result.error and "\n" not in result.error
    second_task = llm.calls[1][1]["content"]
    assert "Static check (pyflakes)" in second_task and "2 |     return x * (" in second_task
