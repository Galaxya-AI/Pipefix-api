"""Access codes, run keys, and the owner of each visitor run."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from pathlib import Path

from flask import abort, request

# One "name:code" per line; no file: every visitor is refused
ACCESS_CODES_FILE = os.getenv("ACCESS_CODES_FILE", "/etc/pipefix/access-codes")
# Address that receives the access code requests ("Ask for one" link of the interface); empty: no link.
ACCESS_REQUEST_EMAIL = os.getenv("ACCESS_REQUEST_EMAIL", "").strip()


class WebOwners:
    """Owner of each web run (GitLab URL, hash of the visitor's run key), kept in memory and in the run's folder.

    The copy on disk lets a visitor follow a run again after PipeFix restarted, when the
    runs folder is a persistent volume. The key itself is never written: only its hash.
    """

    FILE_NAME = "web_owner.json"

    def __init__(self) -> None:
        self.owners: dict[str, tuple[str, str]] = {}
        self.runs_dir: Path | None = None

    def path(self, run_id: str) -> Path | None:
        # A run id is a folder name: refuse anything that could leave the runs folder
        if self.runs_dir is None or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
            return None
        return Path(self.runs_dir) / run_id / self.FILE_NAME

    def __setitem__(self, run_id: str, owner: tuple[str, str]) -> None:
        self.owners[run_id] = owner
        path = self.path(run_id)
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"gitlab_url": owner[0], "key_hash": owner[1]}), encoding="utf-8")
            # Not being able to write only loses the follow-up after a restart
            except OSError:
                pass

    def __getitem__(self, run_id: str) -> tuple[str, str]:
        owner = self.get(run_id)
        if owner is None:
            raise KeyError(run_id)
        return owner

    def get(self, run_id: str) -> tuple[str, str] | None:
        if run_id in self.owners:
            return self.owners[run_id]
        path = self.path(run_id)
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path is not None else None
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or not data.get("gitlab_url") or not data.get("key_hash"):
            return None
        owner = (str(data["gitlab_url"]), str(data["key_hash"]))
        self.owners[run_id] = owner
        return owner


web_owners = WebOwners()


def access_owner(code: str) -> str | None:
    """Return the name of the visitor who holds this access code, or None (no file: nobody)."""
    try:
        lines = Path(ACCESS_CODES_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        name, _, expected = line.strip().partition(":")
        if name and expected and hmac.compare_digest(code.encode(), expected.encode()):
            return name
    return None


def access_request_email(value: str | None = None) -> str:
    """The address of the "Ask for an access code" link, or "" when it is missing or not an email address."""
    address = ACCESS_REQUEST_EMAIL if value is None else value.strip()
    return address if re.fullmatch(r"[^@\s?&#\"'<>]+@[^@\s?&#\"'<>]+\.[^@\s?&#\"'<>]+", address) else ""


def secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def web_owner(run_id: str) -> str:
    """Return the GitLab URL of a web run; 404 unless the X-Run-Key header is the key given when it started."""
    key = request.headers.get("X-Run-Key", "")
    owner = web_owners.get(run_id)
    if owner is None or not hmac.compare_digest(owner[1], secret_hash(key)):
        abort(404, description=f"Unknown run: {run_id}")
    return owner[0]
