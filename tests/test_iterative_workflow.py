"""Offline contract tests: no GitLab token or live runner required."""

from types import SimpleNamespace
from unittest.mock import Mock

from workflow_publication.pipeline_waiter import PipelineWaiter
from workflow_remediation.remediation_workflow import RemediationWorkflow


def make_workflow(outcomes):
    wf = object.__new__(RemediationWorkflow)
    wf.dry_run = False
    wf.wait_for_pipeline = True
    wf.max_rounds = 3
    wf.pipeline_timeout_s = 3
    wf.journal = Mock()
    wf.platform = Mock()
    wf.platform.pipeline_jobs.return_value = [{"id": 1, "name": "tests", "failure_reason": "script_failure"}]
    wf.platform.job_trace.return_value = "failure text"
    wf.platform.branch_exists.return_value = True
    wf.platform.branch_head.return_value = "initial-sha"
    wf.platform.create_commit.side_effect = [
        {"id": f"new-sha-{i}"} for i in range(1, len(outcomes) + 1)
    ]
    wf.platform.find_merge_request.return_value = None
    wf.platform.create_merge_request.return_value = {"web_url": "https://gitlab.example/mr/1"}
    wf.publisher = Mock()
    wf.publisher.commit.side_effect = [
        {"id": f"new-sha-{i}"} for i in range(1, len(outcomes) + 1)
    ]
    wf.publisher.open_merge_request.return_value = {"web_url": "https://gitlab.example/mr/1"}
    wf.remediation_round = Mock()
    wf.remediation_round.run.side_effect = [
        SimpleNamespace(status="fix_ready", as_report=lambda: {}, diff="diff", files={"file": "value"})
        for _ in outcomes
    ]
    wf.waiter = Mock()
    wf.waiter.wait.side_effect = [
        None if status == "timeout" else
        {"id": i, "status": status, "sha": f"new-sha-{i}", "ref": "auto-fix/pipeline-1"}
        for i, status in enumerate(outcomes, 1)
    ]
    return wf


def test_two_rounds_then_one_mr():
    wf = make_workflow(["failed", "success"])
    # Different failure signature after the first round.
    wf.platform.job_trace.side_effect = ["failure one", "failure two"]
    report = {"rounds": []}
    pipeline = {"id": 1, "ref": "main", "sha": "initial-sha"}
    assert wf.run_rounds("group/project", pipeline, False, report) == "pipeline_fixed"
    assert wf.publisher.commit.call_count == 2
    wf.publisher.open_merge_request.assert_called_once()
    assert len(report["validations"]) == 2
    assert [item["verified"] for item in report["validations"]] == [False, True]
    assert report["validation"]["verified"] is True
    assert report["validation"]["commit_sha"] == "new-sha-2"


def test_timeout_does_not_open_mr():
    wf = make_workflow(["timeout"])
    report = {"rounds": []}
    assert wf.run_rounds("group/project", {"id": 1, "sha": "initial-sha"}, False, report) == "pipeline_timeout"
    wf.publisher.open_merge_request.assert_not_called()


def test_repeated_failure_does_not_loop_forever():
    wf = make_workflow(["failed", "failed", "failed"])
    report = {"rounds": []}
    assert wf.run_rounds("group/project", {"id": 1, "sha": "initial-sha"}, False, report) == "repeated_failure"
    wf.publisher.open_merge_request.assert_not_called()


def test_different_failures_still_stop_at_the_round_limit():
    wf = make_workflow(["failed", "failed", "failed"])
    wf.platform.job_trace.side_effect = ["failure one", "failure two", "failure three"]
    report = {"rounds": []}
    assert wf.run_rounds("group/project", {"id": 1, "sha": "initial-sha"}, False, report) == "max_rounds_reached"
    assert wf.publisher.commit.call_count == wf.max_rounds
    assert report["validation"] == {"verified": False, "reason": "max_rounds_reached"}
    wf.publisher.open_merge_request.assert_not_called()


def test_waiter_requires_correct_sha():
    platform = Mock()
    platform.list_pipelines.return_value = [{"id": 9, "sha": "another-sha", "status": "success"}]
    journal = Mock()
    waiter = PipelineWaiter(platform, journal, sleep=lambda _: None)
    assert waiter.wait("group/project", "branch", "correct-sha", timeout_s=0) is None
