"""Offline tests: the code of the failed tests given to the agents; no ghost round in the visitor API."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

import workflow_analysis.remediation_round as remediation_round
from test_iterative_workflow import make_workflow
from text_tools.failed_tests import failed_test_sources, failed_tests, function_source
from workflow_analysis.remediation_round import RemediationRound

TEST_FILE = """\
from decimal import Decimal

import pytest

from src.finance import allocate_payment


def test_allocate_payment_preserves_total():
    shares = allocate_payment(Decimal('0.01'), [1, 1, 1])
    assert sum(shares) == Decimal('0.01')


class TestPages:
    @pytest.mark.parametrize("page", [1])
    def test_first_page(self, page):
        assert page == 1
"""

LOG = """\
=========================== short test summary info ============================
FAILED tests/test_finance.py::test_allocate_payment_preserves_total - AssertionError: assert 0 == 1
FAILED tests/test_finance.py::TestPages::test_first_page[1] - assert 2 == 1
FAILED tests/test_finance.py::test_allocate_payment_preserves_total - AssertionError: again
ERROR tests/test_missing.py::test_gone - FileNotFoundError
"""


def test_failed_tests_are_read_from_the_summary_lines() -> None:
    assert failed_tests(LOG.splitlines()) == [
        ("tests/test_finance.py", ["test_allocate_payment_preserves_total"]),
        ("tests/test_finance.py", ["TestPages", "test_first_page"]),
        ("tests/test_missing.py", ["test_gone"]),
    ]


def test_function_source_keeps_decorators_and_finds_methods() -> None:
    method = function_source(TEST_FILE, ["TestPages", "test_first_page"])
    assert method is not None and method.lstrip().startswith('@pytest.mark.parametrize("page", [1])')
    assert function_source(TEST_FILE, ["test_unknown"]) is None
    assert function_source("def broken(:\n", ["broken"]) is None


def test_failed_test_sources_read_each_file_once_and_skip_missing_ones() -> None:
    reads: list[str] = []

    def read(path: str) -> str:
        reads.append(path)
        if path == "tests/test_missing.py":
            raise FileNotFoundError(path)
        return TEST_FILE

    text = failed_test_sources(read, LOG.splitlines())
    assert reads == ["tests/test_finance.py", "tests/test_missing.py"]
    assert "### tests/test_finance.py::test_allocate_payment_preserves_total" in text
    assert "allocate_payment(Decimal('0.01'), [1, 1, 1])" in text
    assert "### tests/test_finance.py::TestPages::test_first_page" in text
    assert "test_missing" not in text
    assert "more failed tests" in failed_test_sources(read, LOG.splitlines(), max_chars=200)


def test_every_agent_gets_the_code_of_the_failed_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(remediation_round, "build_run_registry", lambda *args: Mock())
    platform = Mock()
    platform.pipeline_jobs.return_value = [{"id": 5, "name": "unit-tests", "failure_reason": "script_failure"}]
    platform.job_trace.return_value = LOG
    platform.file_raw.return_value = TEST_FILE
    runner = Mock()
    runner.run.side_effect = remediation_round.AgentRunError("stop after the triage")
    remediation = RemediationRound(platform, runner, [], Mock(), Mock(), dry_run=True)

    remediation.run("group/project", {"id": 7, "sha": "abc"}, allow_research=False)

    triage_task = runner.run.call_args.args[1]
    assert "## Code of the failed tests (as in the analysed commit)" in triage_task
    assert "shares = allocate_payment(Decimal('0.01'), [1, 1, 1])" in triage_task
    platform.file_raw.assert_any_call("group/project", "tests/test_finance.py", "abc")


def test_a_repeated_failure_does_not_show_a_ghost_round() -> None:
    wf = make_workflow(["failed", "failed", "failed"])
    report: dict = {"rounds": []}
    assert wf.run_rounds("group/project", {"id": 1, "sha": "initial-sha"}, False, report) == "repeated_failure"
    rounds = [c.args[2]["number"] for c in wf.journal.log.call_args_list if c.args[:2] == ("workflow", "round")]
    # Round 2 stops at once on the same errors: only round 1, where the agents worked, is shown
    assert rounds == [1]
