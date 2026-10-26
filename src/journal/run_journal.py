"""Run journal: a trace of everything the agents do during one run.

Each run gets its own folder in runs/ holding journal.jsonl (one JSON event per
line) and the other files of the run (report.json, fix.patch). Events are also
printed briefly to stderr.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

RUNS_DIR = os.getenv("RUNS_DIR", "runs")

JOURNAL_FILE_NAME = "journal.jsonl"

REPORT_FILE_NAME = "report.json"


def new_run_id() -> str:
    """Return an identifier such as "20261022-233105-a1b2c3"."""
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def summarize(data: dict[str, Any], limit: int = 180) -> str:
    """Return a short one-line summary for the terminal, preceded by " - " (empty without data)."""
    if not data:
        return ""
    text = json.dumps(data, ensure_ascii=False, default=str)
    if len(text) > limit:
        text = text[:limit] + "..."
    return f" - {text}"


class RunJournal:
    def __init__(self, runs_dir: str | Path = RUNS_DIR, run_id: str | None = None, verbose: bool = True) -> None:
        self.run_id = run_id or new_run_id()
        self.folder = Path(runs_dir) / self.run_id
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / JOURNAL_FILE_NAME
        self.verbose = verbose
        # The API may write from several threads
        self.lock = threading.Lock()

    def log(self, actor: str, event: str, data: dict[str, Any] | None = None) -> None:
        details = data or {}
        record = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "actor": actor,
            "event": event,
            "data": details,
        }
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self.lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        if self.verbose:
            print(f"[{actor}] {event}{summarize(details)}", file=sys.stderr)

    def write_text(self, name: str, text: str) -> Path:
        """Write a text file into the run folder and return its path."""
        target = self.folder / name
        target.write_text(text, encoding="utf-8")
        return target

    def write_report(self, report: dict[str, Any]) -> Path:
        return self.write_text(REPORT_FILE_NAME, json.dumps(report, ensure_ascii=False, indent=2, default=str))
