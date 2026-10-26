"""Status backends: where the status of the runs not yet finished is kept.

- MemoryStatusBackend: in the API process (development, one process only).
- RedisStatusBackend: in Redis, shared by the API and the workers, and kept
  across restarts when Redis persistence is enabled (production).
A finished run has its report on disk (runs/<run_id>/report.json): its status
entry is then removed.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Protocol

from redis import Redis

# Prefix of every key written by the system in Redis
REDIS_KEY_PREFIX = "ci-remediation"

# Lifetime of a failed run status in Redis, in seconds (7 days)
FAILED_STATUS_TTL_S = 7 * 24 * 3600


class StatusBackend(Protocol):
    """Storage of the statuses of the runs not yet finished."""

    def set(self, run_id: str, status: dict[str, Any], ttl_s: int | None = None) -> None:
        """Store the status of a run, replacing the previous one."""
        ...

    def get(self, run_id: str) -> dict[str, Any] | None: ...

    def all(self) -> dict[str, dict[str, Any]]:
        """Return every stored status, by run id."""
        ...

    def delete(self, run_id: str) -> None: ...


class MemoryStatusBackend:
    """Statuses kept in memory, for one process (development and tests)."""

    def __init__(self) -> None:
        self.statuses: dict[str, dict[str, Any]] = {}
        # One thread per run writes here.
        self.lock = threading.Lock()

    def set(self, run_id: str, status: dict[str, Any], ttl_s: int | None = None) -> None:
        """Store a copy of the status; the lifetime is ignored in memory."""
        with self.lock:
            self.statuses[run_id] = dict(status)

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self.lock:
            status = self.statuses.get(run_id)
        # A copy, so the caller cannot change the stored one.
        return dict(status) if status is not None else None

    def all(self) -> dict[str, dict[str, Any]]:
        with self.lock:
            return {run_id: dict(status) for run_id, status in self.statuses.items()}

    def delete(self, run_id: str) -> None:
        with self.lock:
            self.statuses.pop(run_id, None)


class RedisStatusBackend:
    """Statuses kept in Redis as JSON, shared by the API and the workers."""

    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    def key(self, run_id: str) -> str:
        return f"{REDIS_KEY_PREFIX}:status:{run_id}"

    def set(self, run_id: str, status: dict[str, Any], ttl_s: int | None = None) -> None:
        """Store the status of a run; ttl_s=None keeps it until it is deleted."""
        self.redis.set(self.key(run_id), json.dumps(status, default=str), ex=ttl_s)

    def get(self, run_id: str) -> dict[str, Any] | None:
        raw: Any = self.redis.get(self.key(run_id))
        return json.loads(raw) if raw else None

    def all(self) -> dict[str, dict[str, Any]]:
        statuses: dict[str, dict[str, Any]] = {}
        # scan_iter does not block Redis, unlike KEYS.
        for key in self.redis.scan_iter(match=self.key("*")):
            run_id = (key.decode() if isinstance(key, bytes) else str(key)).rsplit(":", 1)[-1]
            status = self.get(run_id)
            # The status may have expired since the scan.
            if status is not None:
                statuses[run_id] = status
        return statuses

    def delete(self, run_id: str) -> None:
        self.redis.delete(self.key(run_id))
