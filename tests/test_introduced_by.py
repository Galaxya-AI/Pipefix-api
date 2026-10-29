"""The diagnosis names the commit that introduced the error; the merge request links to it."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents.diagnostic_agent import DiagnosisOutput
from workflow_publication.publication_texts import merge_request_description
from workflow_remediation.round_result import RoundResult

BASE = {
    "summary": "add subtracts",
    "root_cause": "commit 6a7d016b changed + into - in add()",
    "files": ["calculator.py"],
    "evidence": "calculator.py line 10",
    "fix_plan": "use + in add()",
    "confidence": "high",
}


def test_commit_is_linked_in_the_merge_request() -> None:
    diagnosis = DiagnosisOutput.model_validate({**BASE, "introduced_by": "6a7d016b320df4969783d0b3466bb1277180c796"})
    pipeline = {"id": 1, "sha": "6a7d016b", "web_url": "https://gitlab.com/g/app/-/pipelines/1"}
    text = merge_request_description(pipeline, RoundResult(pipeline_id=1, diagnosis=diagnosis))
    assert "**Introduced by**: commit [`6a7d016b`](https://gitlab.com/g/app/-/commit/6a7d016b320df49" in text


def test_no_commit_no_line() -> None:
    text = merge_request_description(
        {"id": 1}, RoundResult(pipeline_id=1, diagnosis=DiagnosisOutput.model_validate(BASE))
    )
    assert "Introduced by" not in text


@pytest.mark.parametrize("sha", ["latest", "6a7d", "HEAD~1"])
def test_commit_must_be_a_sha(sha: str) -> None:
    with pytest.raises(ValidationError):
        DiagnosisOutput.model_validate({**BASE, "introduced_by": sha})
