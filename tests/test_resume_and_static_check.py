"""Resume a PipeFix fix branch whose pipeline still fails; static check of the proposed change."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

import workflow_remediation.remediation_workflow as remediation_workflow
from changeset.static_check import added_problems
from workflow_analysis.pipeline_selection import origin_pipeline_id, refusal_status
from workflow_remediation.remediation_workflow import RemediationWorkflow

BEFORE = "from decimal import Decimal, ROUND_HALF_UP\n\nx = Decimal('1').quantize(Decimal('0.01'))\n"


def test_static_check_reports_only_the_problems_the_change_adds() -> None:
    # The fixer removed the import and then used the name: the bug seen on demo-ci-project.
    broken = "from decimal import Decimal\n\nx = Decimal('1').quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)\n"
    problems = added_problems({"src/finance.py": (BEFORE, broken)})
    assert len(problems) == 1 and "undefined name 'ROUND_HALF_UP'" in problems[0]
    # The unused import already in the file is not blamed on a correct fix.
    correct = BEFORE.replace("x = ", "y = ")
    assert added_problems({"src/finance.py": (BEFORE, correct)}) == []
    assert added_problems({"README.md": ("a", "b")}) == []
    assert "syntax error" in added_problems({"a.py": ("x = 1\n", "x = (\n")})[0]


@pytest.mark.parametrize(
    ("branch", "expected"),
    [
        ("auto-fix/pipeline-3010187876-20261030-214528-c366a8", 3010187876),
        ("auto-fix/pipeline-3007645697-retry-20261030-154301-757705", 3007645697),
        ("auto-fix/pipeline-42", 42),
        ("auto-fix/other", None),
        ("main", None),
    ],
)
def test_origin_pipeline_of_a_fix_branch(branch: str, expected: int | None) -> None:
    assert origin_pipeline_id(branch) == expected


def test_failed_fix_branch_pipeline_is_accepted_only_when_resumable() -> None:
    assert refusal_status({"status": "failed", "ref": "auto-fix/pipeline-42-run"}) is None
    assert refusal_status({"status": "failed", "ref": "auto-fix/other"}) == "fix_branch_ignored"


def make_workflow(monkeypatch: pytest.MonkeyPatch, head: str) -> RemediationWorkflow:
    monkeypatch.setattr(
        remediation_workflow,
        "check_project_policy",
        lambda *args: SimpleNamespace(allowed=True, allow_research=False, reason=""),
    )
    wf = object.__new__(RemediationWorkflow)
    wf.dry_run = False
    wf.wait_for_pipeline = True
    wf.max_rounds = 3
    wf.pipeline_timeout_s = 3
    wf.agents = {}
    wf.policy = Mock()
    wf.policy.as_dict.return_value = {}
    wf.journal = Mock(run_id="run-2")
    wf.runner = Mock()
    wf.runner.usage.total_calls.return_value = 0
    wf.runner.usage.as_dict.return_value = {}
    wf.notifier = Mock()
    wf.platform = Mock()
    origin = {"id": 41, "ref": "add-pipefix-challenges", "sha": "origin-sha", "status": "failed"}
    red = {"id": 50, "ref": "auto-fix/pipeline-41-run-1", "sha": "fix-sha", "status": "failed"}
    wf.platform.get_pipeline.side_effect = lambda project, number: {41: origin, 50: red}[number]
    wf.platform.branch_head.side_effect = lambda project, branch: (
        head if branch.startswith("auto-fix/") else "origin-sha"
    )
    wf.platform.branch_exists.return_value = True
    wf.platform.pipeline_jobs.return_value = [{"id": 1, "name": "lint"}]
    wf.platform.job_trace.return_value = "F821 Undefined name `ROUND_HALF_UP`"
    wf.publisher = Mock()
    wf.publisher.commit.return_value = {"id": "sha-3"}
    wf.publisher.open_merge_request.return_value = {"web_url": "https://gitlab.example/mr/2"}
    wf.waiter = Mock()
    wf.waiter.wait.return_value = {"id": 51, "status": "success", "sha": "sha-3"}
    wf.remediation_round = Mock()
    wf.remediation_round.run.return_value = SimpleNamespace(
        status="fix_ready", as_report=lambda: {}, diff="d", files={"src/finance.py": "x"}
    )
    return wf


def test_red_fix_branch_pipeline_resumes_on_the_same_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    wf = make_workflow(monkeypatch, head="fix-sha")
    report: dict[str, Any] = wf.run("g/demo", 50)
    assert report["status"] == "pipeline_fixed"
    assert report["fix_branch"] == "auto-fix/pipeline-41-run-1"
    # The agents analysed the red fix-branch pipeline; the commit went on top of that branch.
    assert wf.remediation_round.run.call_args.args[1]["id"] == 50
    assert wf.publisher.commit.call_args.args[1] == "auto-fix/pipeline-41-run-1"
    assert wf.publisher.commit.call_args.kwargs["start_sha"] is None
    # The merge request goes to the branch of the original pipeline.
    assert wf.publisher.open_merge_request.call_args.args[2] == "add-pipefix-challenges"


def test_resume_refuses_a_branch_with_newer_commits(monkeypatch: pytest.MonkeyPatch) -> None:
    wf = make_workflow(monkeypatch, head="someone-else-sha")
    report = wf.run("g/demo", 50)
    assert report["status"] == "fix_branch_moved"
    wf.remediation_round.run.assert_not_called()
    wf.publisher.commit.assert_not_called()
