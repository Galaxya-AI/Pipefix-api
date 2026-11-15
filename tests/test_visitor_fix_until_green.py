"""Visitor API "fix until the pipeline passes": endpoint, round views, saved fix then new rounds."""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from flask.testing import FlaskClient

import workflow_remediation.remediation_workflow as remediation_workflow
from journal.run_journal import RunJournal
from test_visitor_api import client, fake_run, services, wait_for_runs  # noqa: F401  (pytest fixtures)
from visitor_api.views import round_views
from workflow_remediation.remediation_workflow import RemediationWorkflow

PIPELINE = {"id": 41, "ref": "main", "sha": "analysed-sha", "web_url": "https://gitlab.example/p/41"}


def start_analysis(client: FlaskClient, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:  # noqa: F811
    app_module = sys.modules["app"]
    monkeypatch.setattr(app_module, "build_user_run_function", lambda url, token, tools: fake_run)
    monkeypatch.setattr(app_module.GitLabClient, "get_project", lambda self, project: {"id": 1})
    body = {"access_code": "good-code", "project_url": "https://gitlab.com/g/app", "gitlab_token": "t"}
    answer: dict[str, Any] = client.post("/web/runs", json=body).get_json()
    wait_for_runs()
    return answer


def fake_loop(project: str, pipeline_id: int | None, dry_run: bool, journal: RunJournal) -> dict[str, Any]:
    """Two rounds: the saved fix, then one more fix after a red pipeline."""
    journal.log("workflow", "round", {"number": 1, "pipeline": 41})
    journal.log(
        "remediation_round",
        "errors",
        {
            "pipeline": 41,
            "pipeline_url": "u41",
            "jobs": [{"job": "unit-tests", "url": "j1", "lines": ["E   ImportError"]}],
        },
    )
    result = {"number": 1, "status": "fix_ready", "cause": "path", "files": ["pytest.ini"]}
    journal.log("workflow", "round_result", result)
    journal.log("workflow", "validation", {"number": 1, "commit_sha": "c1", "pipeline_id": 42, "status": "failed"})
    journal.log("workflow", "round", {"number": 2, "pipeline": 42})
    journal.log("workflow", "validation", {"number": 2, "commit_sha": "c2", "pipeline_id": 43, "status": "success"})
    report = {"project": project, "status": "pipeline_fixed", "merge_request": "https://gitlab.example/mr/7"}
    journal.write_report(report)
    return report


def test_fix_until_green_starts_one_loop_followed_with_the_same_key(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch  # noqa: F811
) -> None:
    answer = start_analysis(client, monkeypatch)
    app_module = sys.modules["app"]
    tokens: list[str] = []

    def loop_function(
        url: str, token: str, tools: Any, pipeline: Any, first_result: Any, branch: str, start: Any = None
    ) -> Any:
        tokens.append(token)
        return fake_loop

    saved = (PIPELINE, SimpleNamespace(), "auto-fix/x")
    monkeypatch.setattr(app_module, "load_saved_fix", lambda journal, report: saved)
    monkeypatch.setattr(app_module, "build_user_loop_function", loop_function)
    headers = {"X-Run-Key": answer["run_key"], "X-Gitlab-Token": "api-token"}

    response = client.post(f"/web/runs/{answer['run_id']}/fix-until-green", headers=headers)
    assert response.status_code == 200
    loop_id = response.get_json()["run_id"]
    assert loop_id != answer["run_id"]
    wait_for_runs()
    assert tokens == ["api-token"]

    data = client.get(f"/web/runs/{loop_id}", headers={"X-Run-Key": answer["run_key"]}).get_json()
    assert data["run"]["status"] == "pipeline_fixed"
    assert data["run"]["merge_request"] == "https://gitlab.example/mr/7"
    assert [round_["number"] for round_ in data["rounds"]] == [1, 2]
    assert data["rounds"][0]["errors"][0]["lines"] == ["E   ImportError"]
    assert data["rounds"][1]["result"]["status"] == "success"
    # Another key cannot read the loop; a second click returns the same loop.
    assert client.get(f"/web/runs/{loop_id}", headers={"X-Run-Key": "other"}).status_code == 404
    again = client.post(f"/web/runs/{answer['run_id']}/fix-until-green", headers=headers)
    assert again.get_json()["run_id"] == loop_id
    assert tokens == ["api-token"]


def test_fix_until_green_needs_a_token(client: FlaskClient, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    answer = start_analysis(client, monkeypatch)
    response = client.post(f"/web/runs/{answer['run_id']}/fix-until-green", headers={"X-Run-Key": answer["run_key"]})
    assert response.status_code == 400
    assert "api scope" in response.get_json()["message"]


def test_round_views_follow_the_journal() -> None:
    events = [
        {"actor": "workflow", "event": "start", "data": {}},
        {"actor": "workflow", "event": "round", "data": {"number": 1, "pipeline": 41}},
        {
            "actor": "remediation_round",
            "event": "errors",
            "data": {"pipeline_url": "u", "jobs": [{"job": "lint", "lines": ["F401"]}]},
        },
        {
            "actor": "workflow",
            "event": "round_result",
            "data": {"number": 1, "cause": "unused", "fix": "removed", "files": ["a.py"]},
        },
        {"actor": "diagnosis", "event": "tool_call", "data": {"tool": "search_code"}},
        {
            "actor": "workflow",
            "event": "validation",
            "data": {"commit_sha": "c1", "pipeline_id": 42, "status": "failed"},
        },
    ]
    [view] = round_views(events)
    assert view["errors"] == [{"job": "lint", "url": None, "lines": ["F401"]}]
    assert view["cause"] == "unused" and view["files"] == ["a.py"]
    assert view["commit"]["sha"] == "c1" and view["result"] == {"status": "failed", "pipeline": 42, "url": None}
    assert "search_code" not in str(view)


def test_saved_fix_is_committed_first_then_new_rounds_until_green(monkeypatch: pytest.MonkeyPatch) -> None:
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
    wf.journal = Mock(run_id="run-1")
    wf.runner = Mock()
    wf.runner.usage.total_calls.return_value = 0
    wf.runner.usage.as_dict.return_value = {}
    wf.notifier = Mock()
    wf.platform = Mock()
    wf.platform.pipeline_jobs.return_value = [{"id": 1, "name": "unit-tests"}]
    wf.platform.job_trace.side_effect = ["E   ImportError: no module", "FAILED test_add - assert 1 == 2"]
    wf.platform.branch_exists.return_value = False
    wf.platform.branch_head.return_value = "analysed-sha"
    wf.publisher = Mock()
    wf.publisher.commit.side_effect = [{"id": "sha-1"}, {"id": "sha-2"}]
    wf.publisher.open_merge_request.return_value = {"web_url": "https://gitlab.example/mr/1"}
    wf.waiter = Mock()
    wf.waiter.wait.side_effect = [
        {"id": 42, "status": "failed", "sha": "sha-1"},
        {"id": 43, "status": "success", "sha": "sha-2"},
    ]
    second = SimpleNamespace(status="fix_ready", as_report=lambda: {}, diff="d", files={"src/a.py": "x"})
    wf.remediation_round = Mock()
    wf.remediation_round.run.return_value = second
    first = SimpleNamespace(status="fix_ready", as_report=lambda: {}, diff="d", files={"pytest.ini": "x"})

    report = wf.run_saved_fix("g/app", PIPELINE, first, "auto-fix/pipeline-41")  # type: ignore[arg-type]

    assert report["status"] == "pipeline_fixed"
    assert report["merge_request"] == "https://gitlab.example/mr/1"
    # Round 1 used the fix the visitor accepted: the agents only ran for round 2, on the red fix pipeline.
    wf.remediation_round.record_errors.assert_called_once_with("g/app", PIPELINE)
    assert wf.remediation_round.run.call_count == 1
    assert wf.remediation_round.run.call_args.args[1]["id"] == 42
    assert wf.publisher.commit.call_args_list[0].args[2] is first
    assert wf.publisher.commit.call_args_list[0].kwargs["start_sha"] == "analysed-sha"
    wf.publisher.open_merge_request.assert_called_once()


def test_visitor_sees_why_an_agent_stopped() -> None:
    from visitor_api.views import public_run

    run = {"status": "reviewer_failed", "rounds": [{"error": "LLM call limit of the run reached (20)."}]}
    shown = public_run(run)
    assert shown is not None
    assert "MAX_LLM_CALLS_PER_RUN" in shown["rounds"][0]["error"]
    secret = {"status": "fixer_failed", "rounds": [{"error": "Traceback ... sk-secret-key in /app/src/x.py"}]}
    hidden = public_run(secret)
    assert hidden is not None and hidden["rounds"][0]["error"] is None
