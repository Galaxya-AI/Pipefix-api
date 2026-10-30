"""Web access, URL parsing, and presentation helpers for PipeFix."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from flask import abort, request

from workflow_publication.publisher import MERGE_REQUEST_FILE

ACCESS_CODES_FILE = os.getenv("ACCESS_CODES_FILE", "/etc/pipefix/access-codes")
PIPELINE_IN_URL = re.compile(r"/-/pipelines/(\d+)")
STAGES = ("triage", "diagnosis", "fixer", "reviewer")
web_owners: dict[str, tuple[str, str]] = {}


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


def secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def parse_project_url(address: str) -> tuple[str, str, int | None]:
    """Split a GitLab project address into (instance URL, project path, pipeline number or None).

    Raises:
        ValueError: not an https address of a GitLab project (with the reason, shown to the visitor).
    """
    parts = urlsplit(address.strip())
    if parts.scheme != "https" or not parts.netloc:
        raise ValueError("The project address must start with https://, for example https://gitlab.com/group/project.")
    path = parts.path
    match = PIPELINE_IN_URL.search(path)
    pipeline_id = int(match.group(1)) if match else None
    # GitLab pages of a project live under "/-/": keep only the project part
    path = path.split("/-/", 1)[0].strip("/").removesuffix(".git")
    segments = [segment for segment in path.split("/") if segment]
    if len(segments) < 2:
        raise ValueError("Give the address of a project, for example https://gitlab.com/group/project.")
    return f"https://{parts.netloc}", "/".join(segments), pipeline_id


def web_owner(run_id: str) -> str:
    """Return the GitLab URL of a web run; 404 unless the X-Run-Key header is the key given when it started."""
    key = request.headers.get("X-Run-Key", "")
    owner = web_owners.get(run_id)
    if owner is None or not hmac.compare_digest(owner[1], secret_hash(key)):
        abort(404, description=f"Unknown run: {run_id}")
    return owner[0]


def stage_states(events: list[dict[str, Any]], finished: bool) -> list[dict[str, str]]:
    """State of each agent: pending, running, done, or stopped when the run ended during it."""
    started = {event.get("actor") for event in events if event.get("event") == "start"}
    done = {event.get("actor") for event in events if event.get("event") == "finish"}
    states = []
    for stage in STAGES:
        if stage in done:
            state = "done"
        elif stage in started:
            state = "stopped" if finished else "running"
        else:
            state = "pending"
        states.append({"name": stage, "state": state})
    return states


def published_merge_request(run_id: str, store: Any) -> str | None:
    folder = store.folder(run_id)
    if folder is None or not (folder / MERGE_REQUEST_FILE).is_file():
        return None
    return (folder / MERGE_REQUEST_FILE).read_text(encoding="utf-8")

