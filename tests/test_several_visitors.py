"""Offline tests: several visitors at once (runs at the same time, one run per project, daily limit, LLM spacing)."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from app_runs.daily_limit import DailyLimit
from app_runs.run_store import RunStore
from app_runs.thread_launcher import RunSlots, ThreadLauncher
from llm.spacing import CallSpacing


class Recorder:
    """A run function that notes how many runs work at the same time."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.now = 0
        self.most = 0
        self.projects: list[str] = []

    def __call__(self, project: str, pipeline_id: Any, dry_run: bool, journal: Any) -> None:
        with self.lock:
            self.now += 1
            self.most = max(self.most, self.now)
            self.projects.append(project)
        time.sleep(0.05)
        with self.lock:
            self.now -= 1


def test_at_most_the_allowed_runs_work_at_once_and_the_others_wait(tmp_path: Path) -> None:
    recorder = Recorder()
    store = RunStore(runs_dir=tmp_path)
    launcher = ThreadLauncher(recorder, store, slots=RunSlots(2))
    ids = [launcher.launch(f"group/project-{i}", None, dry_run=True) for i in range(5)]
    assert {(store.get_run(run_id) or {}).get("status", "done") for run_id in ids} <= {"queued", "running", "done"}
    launcher.wait()
    assert recorder.most == 2
    assert len(recorder.projects) == 5


def test_two_runs_on_the_same_project_never_work_at_once(tmp_path: Path) -> None:
    recorder = Recorder()
    launcher = ThreadLauncher(recorder, RunStore(runs_dir=tmp_path), slots=RunSlots(4))
    for _ in range(3):
        launcher.launch("group/same", None, dry_run=True)
    launcher.wait()
    assert recorder.most == 1
    assert recorder.projects == ["group/same"] * 3


def test_daily_limit_per_visitor_survives_a_restart(tmp_path: Path) -> None:
    limit = DailyLimit(tmp_path, limit=2)
    assert limit.take("alice") and limit.take("alice")
    assert not limit.take("alice")
    assert limit.take("bob")
    # A new pod reads the counts of today from the runs folder.
    assert not DailyLimit(tmp_path, limit=2).take("alice")
    assert DailyLimit(tmp_path, limit=0).take("alice")


def test_llm_calls_of_every_thread_are_spaced() -> None:
    clock = [100.0]
    waits: list[float] = []
    spacing = CallSpacing(0.5, clock=lambda: clock[0], sleep=waits.append)
    for _ in range(3):
        spacing.wait_turn()
    assert waits == [0.5, 1.0]
    clock[0] = 200.0
    spacing.wait_turn()
    assert waits == [0.5, 1.0]
