"""Without a pipeline number, the latest failure not fixed since is analysed."""

from __future__ import annotations

from typing import Any

from workflow_analysis.pipeline_selection import latest_failed_pipeline


class Pipelines:
    def __init__(self, pipelines: list[dict[str, Any]]) -> None:
        self.pipelines = pipelines

    def list_pipelines(self, project: str, **filters: Any) -> list[dict[str, Any]]:
        return self.pipelines


def test_failure_fixed_by_a_newer_success_is_skipped() -> None:
    platform = Pipelines(
        [
            {"id": 7, "ref": "add-errors", "status": "success"},
            {"id": 6, "ref": "auto-fix/pipeline-4", "status": "success"},
            {"id": 5, "ref": "auto-fix/pipeline-4", "status": "failed"},
            {"id": 4, "ref": "add-errors", "status": "failed"},
            {"id": 3, "ref": "main", "status": "failed"},
        ]
    )
    assert latest_failed_pipeline(platform, "g/app") == {"id": 3, "ref": "main", "status": "failed"}  # type: ignore[arg-type]


def test_nothing_to_fix_when_every_branch_passed_since() -> None:
    platform = Pipelines([{"id": 2, "ref": "main", "status": "success"}, {"id": 1, "ref": "main", "status": "failed"}])
    assert latest_failed_pipeline(platform, "g/app") is None  # type: ignore[arg-type]
