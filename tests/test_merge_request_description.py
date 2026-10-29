"""The merge request description does not repeat the diff that GitLab shows in its Changes tab."""

from __future__ import annotations

from workflow_publication.publication_texts import merge_request_description
from workflow_remediation.round_result import RoundResult


def test_description_has_no_diff_copy() -> None:
    result = RoundResult(pipeline_id=1, diff="--- a/app.py\n+++ b/app.py\n-old\n+new\n")
    text = merge_request_description({"id": 1, "sha": "abc"}, result)
    assert "```diff" not in text
    assert "+new" not in text
    assert "review it before merging" in text
