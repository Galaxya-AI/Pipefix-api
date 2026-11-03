"""Offline tests: lint and format errors fixed by ruff without any LLM call."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

import workflow_analysis.remediation_round as remediation_round
from changeset.autofix import lint_report, ruff_autofix
from workflow_analysis.remediation_round import RemediationRound

FILES = {
    "src/app.py": "import os\nimport sys\n\n\ndef main():\n    return sys.argv\n",
    "src/ugly.py": "x  =  {'a':1}\n",
    "pyproject.toml": "[tool.ruff]\nline-length = 100\n",
}
LINT_LOG = """\
$ ruff check . --output-format=concise
src/app.py:1:8: F401 [*] `os` imported but unused
Found 1 error.
[*] 1 fixable with the `--fix` option.
$ ruff format --check .
Would reformat: src/ugly.py
1 file would be reformatted
"""
TEST_LOG = "FAILED tests/test_app.py::test_main - assert 1 == 2\n"


def read(path: str) -> str:
    if path not in FILES:
        raise FileNotFoundError(path)
    return FILES[path]


def test_lint_report_reads_ruff_errors_and_files_to_reformat() -> None:
    report = lint_report([LINT_LOG])
    assert report.codes == {"F401"}
    assert report.check_paths == {"src/app.py"}
    assert report.format_paths == {"src/ugly.py"}
    assert report.only_ruff
    assert not lint_report([LINT_LOG, TEST_LOG]).only_ruff
    # A path that leaves the repository is never used
    assert lint_report(["../etc/x.py:1:1: F401 unused\n"]).check_paths == set()


def test_ruff_fixes_only_the_reported_rules_and_reformats() -> None:
    result = ruff_autofix(read, lint_report([LINT_LOG]))
    assert result.files["src/app.py"] == "import sys\n\n\ndef main():\n    return sys.argv\n"
    assert result.files["src/ugly.py"] == 'x = {"a": 1}\n'
    assert result.remaining == []
    assert result.complete()


def make_round(log: str, monkeypatch: pytest.MonkeyPatch) -> tuple[RemediationRound, Mock]:
    monkeypatch.setattr(remediation_round, "build_run_registry", lambda *args: Mock())
    platform = Mock()
    platform.pipeline_jobs.return_value = [{"id": 3, "name": "lint", "failure_reason": "script_failure"}]
    platform.job_trace.return_value = log
    platform.file_raw.side_effect = lambda project, path, sha: read(path)
    runner = Mock()
    runner.run.side_effect = remediation_round.AgentRunError("agents not expected")
    return RemediationRound(platform, runner, [], Mock(), Mock(), dry_run=True), runner


def test_a_lint_only_failure_is_fixed_without_any_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    remediation, runner = make_round(LINT_LOG, monkeypatch)
    result = remediation.run("group/app", {"id": 9, "sha": "abc"}, allow_research=False)
    assert result.status == "fix_ready"
    runner.run.assert_not_called()
    assert sorted(result.files) == ["src/app.py", "src/ugly.py"]
    assert "-import os" in result.diff
    assert result.review is not None and result.review.approved


def test_other_errors_still_go_to_the_agents_with_the_ruff_fixes(monkeypatch: pytest.MonkeyPatch) -> None:
    remediation, runner = make_round(LINT_LOG + TEST_LOG, monkeypatch)
    result = remediation.run("group/app", {"id": 9, "sha": "abc"}, allow_research=False)
    task = runner.run.call_args.args[1]
    assert "## Already fixed automatically by ruff" in task and "src/app.py" in task
    assert result.status == "triage_failed"
