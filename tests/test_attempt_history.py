"""Offline tests: the earlier fix attempts are given to the agents, so they do not try one again."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

from test_iterative_workflow import make_workflow
from workflow_analysis.attempt_history import Attempt, earlier_pipelines, errors_of, history_text

LOG = "FAILED tests/test_finance.py::test_allocate_payment_preserves_total - assert 0 == 1\n"


def test_no_history_for_a_first_fix_on_a_normal_branch() -> None:
    assert history_text({"ref": "main"}, [], []) == ""


def test_history_lists_the_attempts_of_this_run_and_the_earlier_pipelines() -> None:
    attempt = Attempt(round=1, commit="abcdef123456", diff="-old\n+new\n", errors_after=errors_of({1: LOG}))
    earlier = [{"id": 30, "sha": "1234567890"}]
    text = history_text({"ref": "auto-fix/pipeline-3-run"}, earlier, [attempt])
    assert "never restore code that an earlier attempt already had" in text
    assert "is a PipeFix fix branch" in text
    assert "- pipeline #30: commit 12345678" in text
    assert "### Round 1 of this run: commit abcdef12" in text and "+new" in text
    assert "FAILED tests/test_finance.py::test_allocate_payment_preserves_total" in text


def test_earlier_pipelines_keep_only_the_failed_ones_of_a_fix_branch() -> None:
    platform = Mock()
    platform.list_pipelines.return_value = [
        {"id": 50, "status": "failed", "sha": "c"},
        {"id": 49, "status": "success", "sha": "b"},
        {"id": 48, "status": "failed", "sha": "a"},
    ]
    found = earlier_pipelines(platform, "group/app", {"id": 50, "ref": "auto-fix/pipeline-41-run"})
    assert [item["id"] for item in found] == [48]
    assert earlier_pipelines(platform, "group/app", {"id": 7, "ref": "main"}) == []
    platform.list_pipelines.side_effect = RuntimeError("GitLab down")
    assert earlier_pipelines(platform, "group/app", {"id": 50, "ref": "auto-fix/x"}) == []


def test_the_second_round_gets_the_first_attempt_and_its_errors() -> None:
    wf = make_workflow(["failed", "success"])
    wf.platform.job_trace.side_effect = ["first errors", LOG]
    wf.platform.list_pipelines.return_value = []
    wf.remediation_round.run.side_effect = [
        SimpleNamespace(status="fix_ready", as_report=lambda: {}, diff=f"diff {i}", files={"f": "v"}) for i in (1, 2)
    ]
    report: dict = {"rounds": []}
    assert wf.run_rounds("group/project", {"id": 1, "ref": "main", "sha": "initial-sha"}, False, report) == (
        "pipeline_fixed"
    )
    first, second = wf.remediation_round.run.call_args_list
    assert first.kwargs["history"] == ""
    history = second.kwargs["history"]
    assert "### Round 1 of this run: commit new-sha-" in history and "diff 1" in history
    assert "FAILED tests/test_finance.py::test_allocate_payment_preserves_total" in history
