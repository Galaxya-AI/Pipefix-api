"""Offline tests: the same failure on the same commit reuses the approved fix, without any LLM call."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

import workflow_analysis.remediation_round as remediation_round
from journal.run_journal import RunJournal
from workflow_analysis.fix_reuse import FixCache, failure_key
from workflow_analysis.remediation_round import RemediationRound
from workflow_remediation.round_result import RoundResult

FILES = {"src/app.py": "import os\nimport sys\n\n\ndef main():\n    return sys.argv\n"}
LOG = "src/app.py:1:8: F401 [*] `os` imported but unused\nFound 1 error.\n"


def make_round(tmp_path: Path, run_id: str, monkeypatch: pytest.MonkeyPatch) -> tuple[RemediationRound, RunJournal]:
    monkeypatch.setattr(remediation_round, "build_run_registry", lambda *args: Mock())
    platform = Mock()
    platform.pipeline_jobs.return_value = [{"id": 3, "name": "lint", "failure_reason": "script_failure"}]
    platform.job_trace.return_value = LOG
    platform.file_raw.side_effect = lambda project, path, sha: FILES[path]
    runner = Mock()
    runner.run.side_effect = remediation_round.AgentRunError("no agent expected")
    journal = RunJournal(tmp_path, run_id, verbose=False)
    return RemediationRound(platform, runner, [], Mock(), journal, dry_run=True), journal


def events(journal: RunJournal) -> list[str]:
    return [line for line in journal.path.read_text().splitlines() if "fix_reused" in line]


def test_the_same_failure_on_the_same_commit_reuses_the_fix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first, first_journal = make_round(tmp_path, "run-1", monkeypatch)
    one = first.run("group/app", {"id": 9, "sha": "abc"}, allow_research=False)
    assert one.status == "fix_ready" and one.reuse_key

    again, journal = make_round(tmp_path, "run-2", monkeypatch)
    # ruff is not even needed: the saved fix comes first
    monkeypatch.setattr(remediation_round, "ruff_autofix", Mock(side_effect=AssertionError("not called")))
    two = again.run("group/app", {"id": 10, "sha": "abc"}, allow_research=False)
    assert two.status == "fix_ready"
    assert two.diff == one.diff
    assert events(journal) and '"from_run": "run-1"' in events(journal)[0]
    assert not events(first_journal)


def test_another_commit_or_a_failed_fix_is_never_reused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first, _ = make_round(tmp_path, "run-1", monkeypatch)
    one = first.run("group/app", {"id": 9, "sha": "abc"}, allow_research=False)

    other, journal = make_round(tmp_path, "run-2", monkeypatch)
    other.run("group/app", {"id": 10, "sha": "def"}, allow_research=False)
    assert not events(journal)

    first.forget_fix(one)
    assert FixCache(tmp_path).load(one.reuse_key) is None


def test_the_key_ignores_durations_but_not_the_errors() -> None:
    jobs = [{"id": 1, "name": "tests"}]
    base = failure_key("p", "sha", jobs, {1: "FAILED tests/a.py::t - assert 1 == 2\n== 1 failed in 0.42s =="})
    same = failure_key("p", "sha", jobs, {1: "FAILED tests/a.py::t - assert 1 == 2\n== 1 failed in 3.10s =="})
    other = failure_key("p", "sha", jobs, {1: "FAILED tests/a.py::u - assert 1 == 2\n"})
    assert base == same and base != other


def test_an_old_entry_is_not_reused(tmp_path: Path) -> None:
    cache = FixCache(tmp_path, ttl_s=60)
    result = RoundResult(pipeline_id=1, files={"a.py": "x"})
    cache.save("k", result, "run-1")
    # Saved without the agents' outputs: not a usable fix
    assert cache.load("k") is None
