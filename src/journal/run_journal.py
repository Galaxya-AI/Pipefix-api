"""Run journal: a trace of everything the agents do during one run.

Each run gets its own folder in runs/ holding journal.jsonl (one JSON event per
line) and the other files of the run (report.json, fix.patch). Events are also
written to the application log, with the module, function and line that sent them.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from types import FrameType
from typing import Any

from logging_module.log_config import logger

RUNS_DIR = os.getenv("RUNS_DIR", "runs")

JOURNAL_FILE_NAME = "journal.jsonl"

REPORT_FILE_NAME = "report.json"


def new_run_id() -> str:
    """Return an identifier such as "20261022-233105-a1b2c3"."""
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


# Words of an event name that make it an error in the application log.
ERROR_WORDS = ("error", "failed", "failure")


def summarize(data: dict[str, Any], limit: int = 400) -> str:
    """Return a short one-line summary for the terminal, preceded by " - " (empty without data)."""
    if not data:
        return ""
    text = json.dumps(data, ensure_ascii=False, default=str)
    if len(text) > limit:
        text = text[:limit] + "..."
    return f" - {text}"


def event_level(event: str, data: dict[str, Any]) -> int:
    """ERROR for a failure (event name or "status" field), INFO otherwise."""
    words = f"{event} {data.get('status', '')}".lower()
    return logging.ERROR if any(word in words for word in ERROR_WORDS) else logging.INFO


def caller_stack_level() -> int:
    """Stack level of the first caller outside the journal and its log() wrappers.

    The log line then shows the module, function and line that really sent the event.
    """
    frame: FrameType | None = sys._getframe(1)
    level = 1
    while frame is not None and (frame.f_code.co_filename == __file__ or frame.f_code.co_name == "log"):
        frame = frame.f_back
        level += 1
    return level


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
            logger.log(
                event_level(event, details),
                "[%s] %s%s (run %s)",
                actor,
                event,
                summarize(details),
                self.run_id,
                stacklevel=caller_stack_level(),
            )

    def write_text(self, name: str, text: str) -> Path:
        """Write a text file into the run folder and return its path."""
        target = self.folder / name
        target.write_text(text, encoding="utf-8")
        return target

    def write_report(self, report: dict[str, Any]) -> Path:
        return self.write_text(REPORT_FILE_NAME, json.dumps(report, ensure_ascii=False, indent=2, default=str))
