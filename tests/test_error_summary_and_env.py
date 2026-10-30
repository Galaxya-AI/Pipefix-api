"""Offline tests: error summary of CI logs, repeated-failure fingerprint, env values, branch creation."""

from types import SimpleNamespace
from unittest.mock import Mock

from startup.env_values import env_bool, env_int
from text_tools.log_cleaning import clean_log, error_summary, failure_fingerprint
from workflow_publication.publisher import Publisher
from workflow_remediation.remediation_workflow import RemediationWorkflow

PYTEST_LOG = """\
$ python -m pytest -v -rfE --continue-on-collection-errors
==================================== ERRORS ====================================
_________________ ERROR collecting tests/test_scheduling.py _________________
ImportError while importing test module '/builds/tests/test_scheduling.py'.
E   ModuleNotFoundError: No module named 'scheduling'
""" + "\n".join(f"tests/test_calculator.py::test_case_{i} PASSED" for i in range(300)) + """
=========================== short test summary info ============================
FAILED tests/test_calculator.py::test_add - assert -1 == 5
FAILED tests/test_calculator.py::test_average - ZeroDivisionError: division by zero
ERROR tests/test_scheduling.py
==================== 2 failed, 300 passed, 1 error in 0.42s ====================
"""

RUFF_LOG = """\
$ ruff check . --select F --output-format=concise
calculator.py:1:8: F401 [*] `os` imported but unused
scheduling.py:12:5: F821 Undefined name `json`
Found 2 errors.
"""


def test_error_summary_lists_every_error():
    lines = error_summary(PYTEST_LOG + RUFF_LOG)
    assert "E   ModuleNotFoundError: No module named 'scheduling'" in lines
    assert "FAILED tests/test_calculator.py::test_add - assert -1 == 5" in lines
    assert "ERROR tests/test_scheduling.py" in lines
    assert "scheduling.py:12:5: F821 Undefined name `json`" in lines
    assert "Found 2 errors." in lines
    assert not any("PASSED" in line for line in lines)


def test_clean_log_keeps_errors_cut_from_the_tail():
    cleaned = clean_log(PYTEST_LOG, max_lines=10)
    # The collection error is far before the last 10 lines, but still given to the agents
    assert "No module named 'scheduling'" in cleaned
    assert cleaned.rstrip().endswith("1 error in 0.42s ====================")


def test_fingerprint_ignores_durations_but_not_new_errors():
    first = failure_fingerprint(PYTEST_LOG)
    same_error_other_run = failure_fingerprint(PYTEST_LOG.replace("in 0.42s", "in 1.07s"))
    next_error = failure_fingerprint(PYTEST_LOG.replace("No module named 'scheduling'", "No module named 'tasks'"))
    assert first == same_error_other_run
    assert first != next_error


def test_env_values_treat_empty_as_default(monkeypatch):
    monkeypatch.setenv("MAX_ROUNDS", "")
    monkeypatch.setenv("WAIT_FOR_PIPELINE", "")
    assert env_int("MAX_ROUNDS", 1) == 1
    assert env_bool("WAIT_FOR_PIPELINE", False) is False
    monkeypatch.setenv("MAX_ROUNDS", "five")
    assert env_int("MAX_ROUNDS", 1) == 1
    monkeypatch.setenv("MAX_ROUNDS", "0")
    assert env_int("MAX_ROUNDS", 1, minimum=1) == 1
    monkeypatch.setenv("MAX_ROUNDS", "5")
    monkeypatch.setenv("WAIT_FOR_PIPELINE", "True")
    assert env_int("MAX_ROUNDS", 1) == 5
    assert env_bool("WAIT_FOR_PIPELINE", False) is True


def test_first_commit_creates_the_branch_in_the_same_call():
    wf = object.__new__(RemediationWorkflow)
    wf.platform = Mock()
    wf.publisher = Mock()
    result = SimpleNamespace()
    pipeline = {"id": 7, "sha": "analysed-sha"}

    wf.platform.branch_exists.return_value = False
    wf.commit_fix("group/project", pipeline, "auto-fix/pipeline-7", result)
    wf.publisher.commit.assert_called_with("group/project", "auto-fix/pipeline-7", result, start_sha="analysed-sha")
    wf.platform.create_branch.assert_not_called()

    wf.platform.branch_exists.return_value = True
    wf.commit_fix("group/project", pipeline, "auto-fix/pipeline-7", result)
    wf.publisher.commit.assert_called_with("group/project", "auto-fix/pipeline-7", result, start_sha=None)


def test_publisher_sends_start_sha_to_the_platform():
    platform = Mock()
    platform.create_commit.return_value = {"id": "new-sha"}
    publisher = Publisher(platform, Mock())
    result = SimpleNamespace(files={"calculator.py": "x = 1\n"}, fix=None, diagnosis=None, pipeline_id=7)
    publisher.commit("group/project", "auto-fix/pipeline-7", result, start_sha="analysed-sha")
    assert platform.create_commit.call_args.kwargs["start_sha"] == "analysed-sha"


def test_gitlab_timestamped_log_lines_are_recognised():
    stamped = "\n".join(
        f"2026-10-30T21:45:{index:02d}.123456Z 00O {line}" for index, line in enumerate(RUFF_LOG.splitlines())
    )
    lines = error_summary(clean_log(stamped, max_lines=0))
    assert "scheduling.py:12:5: F821 Undefined name `json`" in lines
    assert "Found 2 errors." in lines
    assert failure_fingerprint(stamped) == failure_fingerprint(RUFF_LOG)
