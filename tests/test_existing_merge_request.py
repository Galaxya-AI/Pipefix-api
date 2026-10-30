"""An open merge request does not block a fresh diagnosis."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import workflow_remediation.remediation_workflow as workflow_module
from journal.run_journal import RunJournal
from workflow_analysis.policy_check import PolicyDecision
from workflow_remediation.remediation_workflow import RemediationWorkflow


class Platform:
    def __init__(self, open_merge_request: dict[str, Any] | None) -> None:
        self.open_merge_request = open_merge_request
        self.asked: list[str] = []

    def find_merge_request(self, project: str, source_branch: str) -> dict[str, Any] | None:
        self.asked.append(source_branch)
        return self.open_merge_request


class Round:
    def __init__(self) -> None:
        self.calls = 0

    def run(self, *args: Any) -> Any:
        self.calls += 1
        return type("Result", (), {"status": "no_fix", "as_report": lambda self: {"status": "no_fix"}})()


class Notifier:
    def notify(self, *args: Any) -> dict[str, Any]:
        return {}


def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: Platform, round_: Round) -> dict[str, Any]:
    pipeline = {"id": 42, "ref": "main", "status": "failed", "sha": "abc"}
    monkeypatch.setattr(workflow_module, "check_project_policy", lambda *args: PolicyDecision(True, False))
    monkeypatch.setattr(workflow_module, "find_pipeline", lambda *args: pipeline)
    workflow = RemediationWorkflow(
        platform,  # type: ignore[arg-type]
        llm=None,  # type: ignore[arg-type]
        agents={},
        tools=[],
        journal=RunJournal(tmp_path),
        remediation_round=round_,  # type: ignore[arg-type]
        notifier=Notifier(),  # type: ignore[arg-type]
    )
    return workflow.run("group/app", 42)


def test_open_merge_request_allows_independent_analysis(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    platform, round_ = Platform({"web_url": "https://gitlab.com/group/app/-/merge_requests/3"}), Round()
    report = run(tmp_path, monkeypatch, platform, round_)
    assert report["status"] == "no_fix"
    assert report["previous_merge_request_url"].endswith("/merge_requests/3")
    assert report["fix_branch"].startswith("auto-fix/pipeline-42-retry-")
    assert platform.asked == ["auto-fix/pipeline-42"]
    assert round_.calls == 1
    assert report["usage"]["llm_calls"] == 0


def test_without_existing_merge_request_keeps_standard_branch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    platform, round_ = Platform(None), Round()
    report = run(tmp_path, monkeypatch, platform, round_)
    assert report["status"] == "no_fix"
    assert report["fix_branch"] == "auto-fix/pipeline-42"
    assert "previous_merge_request_url" not in report
    assert round_.calls == 1
