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
WEB_OWNER_FILE = ".web-owner.json"


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


def register_web_owner(run_id: str, gitlab_url: str, run_key: str, store: Any) -> None:
    """Persist web-run authorization in the shared run directory.

    Gunicorn may serve consecutive requests from different worker processes, so
    ownership cannot live in a process-local dictionary. Only a SHA-256 hash of
    the run key is stored; the plaintext key is returned to the browser once.
    """
    folder = store.folder(run_id)
    if folder is None:
        raise ValueError(f"Invalid run id: {run_id}")
    folder.mkdir(parents=True, exist_ok=True)
    payload = {"gitlab_url": gitlab_url, "run_key_hash": secret_hash(run_key)}
    target = folder / WEB_OWNER_FILE
    temporary = folder / f"{WEB_OWNER_FILE}.tmp"
    temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    temporary.replace(target)


def web_owner(run_id: str, store: Any) -> str:
    """Return the GitLab URL of a web run after validating its X-Run-Key header."""
    folder = store.folder(run_id)
    target = folder / WEB_OWNER_FILE if folder is not None else None
    try:
        owner = json.loads(target.read_text(encoding="utf-8")) if target is not None else None
    except (OSError, ValueError, TypeError):
        owner = None

    key = request.headers.get("X-Run-Key", "")
    if (
        not isinstance(owner, dict)
        or not isinstance(owner.get("gitlab_url"), str)
        or not isinstance(owner.get("run_key_hash"), str)
        or not hmac.compare_digest(owner["run_key_hash"], secret_hash(key))
    ):
        abort(404, description=f"Unknown run: {run_id}")
    return owner["gitlab_url"]


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

