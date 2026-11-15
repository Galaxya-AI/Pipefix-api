"""Daily limit of visitor runs per access code, to keep the LLM bill under control.

The counts are kept in the runs folder (_daily_runs/<day>.json), so a restart of the
pod does not reset them. DAILY_RUNS_PER_VISITOR=0 removes the limit.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from startup.env_values import env_int

DAILY_RUNS_PER_VISITOR = env_int("DAILY_RUNS_PER_VISITOR", 10, minimum=0)
COUNTS_FOLDER = "_daily_runs"


class DailyLimit:
    def __init__(self, runs_dir: Path | None = None, limit: int = DAILY_RUNS_PER_VISITOR) -> None:
        self.runs_dir = runs_dir
        self.limit = limit
        self.lock = threading.Lock()
        # Counts of one day in one runs folder: (folder, day) -> visitor -> runs.
        self.counts: dict[tuple[str, str], dict[str, int]] = {}

    def path(self, day: str) -> Path | None:
        return Path(self.runs_dir) / COUNTS_FOLDER / f"{day}.json" if self.runs_dir else None

    def read(self, day: str) -> dict[str, int]:
        key = (str(self.runs_dir), day)
        if key in self.counts:
            return self.counts[key]
        counts: dict[str, int] = {}
        path = self.path(day)
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path else {}
            if isinstance(data, dict):
                counts = {str(name): int(value) for name, value in data.items()}
        except (OSError, ValueError, TypeError):
            pass
        # Only today is kept in memory.
        self.counts = {key: counts}
        return counts

    def take(self, visitor: str) -> bool:
        """Count one run for this visitor today; False (nothing counted) when the limit is reached."""
        if self.limit <= 0:
            return True
        day = time.strftime("%Y-%m-%d")
        with self.lock:
            counts = self.read(day)
            if counts.get(visitor, 0) >= self.limit:
                return False
            counts[visitor] = counts.get(visitor, 0) + 1
            path = self.path(day)
            if path:
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(counts), encoding="utf-8")
                except OSError:
                    pass
        return True


daily_limit = DailyLimit()
