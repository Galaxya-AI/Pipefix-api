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

from app_runs.run_store import RunStore
from connectors.errors.errors import ConnectorError
from connectors.gitlab.client import GitLabClient
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


def read_pipeline_number(value: Any) -> int | None:
    """Return the pipeline number typed in the form ("3005959635" or "#3005959635"), None when empty.

    Anything else is refused: a number typed wrongly must never fall back to the latest failed pipeline.
    """
    if value is None or value == "":
        return None
    text = str(value).strip().lstrip("#").strip()
    if not text.isdigit() or int(text) < 1:
        abort(400, description="The pipeline number must be a number, for example 2978771 or #2978771.")
    return int(text)


def check_pipeline_to_fix(gitlab: GitLabClient, project: str, pipeline_id: int) -> None:
    """Answer at once, before any agent runs, when the pipeline is missing or has nothing to fix."""
    try:
        pipeline = gitlab.get_pipeline(project, pipeline_id)
    except ConnectorError as error:
        if error.status_code == 404:
            abort(400, description=f"Pipeline #{pipeline_id} does not exist in the project {project}.")
        raise
    status = str(pipeline.get("status", ""))
    if status == "success":
        abort(400, description=f"Pipeline #{pipeline_id} passed: there is nothing to fix.")
    if status in ("created", "pending", "running", "waiting_for_resource", "preparing", "scheduled"):
        abort(400, description=f"Pipeline #{pipeline_id} is still running: try again once it has failed.")
    if status != "failed":
        abort(400, description=f"Pipeline #{pipeline_id} did not fail (status: {status}): there is nothing to fix.")


def web_owner(run_id: str) -> str:
    """Return the GitLab URL of a web run; 404 unless the X-Run-Key header is the key given when it started."""
    key = request.headers.get("X-Run-Key", "")
    owner = web_owners.get(run_id)
    if owner is None or not hmac.compare_digest(owner[1], secret_hash(key)):
        abort(404, description=f"Unknown run: {run_id}")
    return owner[0]


def agent_failure_reason(error: str) -> str | None:
    """A short reason the visitor can act on when an agent could not finish, or None.

    Only the known limits are shown; any other error text stays in the developers' logs.
    """
    if not error:
        return None
    found = re.search(r"LLM call limit of the run reached \((\d+)\)", error)
    if found:
        return (
            f"The run used all its LLM calls ({found.group(1)}). "
            "Raise MAX_LLM_CALLS_PER_RUN in the settings, then restart PipeFix."
        )
    found = re.search(r"Agent '(\w+)' reached its turn limit \((\d+)\)", error)
    if found:
        return (
            f"The {found.group(1)} agent reached its turn limit ({found.group(2)}). "
            "Raise AGENT_MAX_TURNS in the settings, then restart PipeFix."
        )
    found = re.search(r"Agent '(\w+)' gave \d+ invalid final answers", error)
    if found:
        return f"The {found.group(1)} agent did not give a valid answer. Try again."
    found = re.search(r"LLM API error (4\d\d): .*?\"message\": \"(.+?)\"", error, re.DOTALL)
    if found:
        # The provider refused the request: its message says why (model, parameter, key, quota)
        return f"The LLM provider refused the request (HTTP {found.group(1)}): {found.group(2)[:300]}"
    if error.startswith("LLM call failed"):
        return "The LLM provider did not answer (network, API key or quota). Try again later."
    return None


def public_run(run: dict[str, Any] | None) -> dict[str, Any] | None:
    """Only what the page shows: never the tools the agents called, their prompts, usage or settings."""
    if run is None:
        return None
    last = (run.get("rounds") or [{}])[-1] or {}
    shown = {
        "diagnosis": {
            "root_cause": (last.get("diagnosis") or {}).get("root_cause"),
            "introduced_by": (last.get("diagnosis") or {}).get("introduced_by"),
        },
        "fix": {"summary": (last.get("fix") or {}).get("summary")},
        "review": {"comment": (last.get("review") or {}).get("comment")},
        # An agent that could not finish: only a known limit is shown; other details stay in the logs.
        "error": agent_failure_reason(str(last.get("error") or ""))
        if str(run.get("status", "")).endswith("_failed")
        else last.get("error"),
    }
    keys = ("status", "project", "error", "merge_request_url", "merge_request", "fix_branch")
    view = {key: run.get(key) for key in keys if run.get(key)}
    view["rounds"] = [shown] if run.get("rounds") else []
    return view


def round_views(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per round, built from the journal while the run goes on.

    Each round shows the errors of the pipeline it starts from, what the agents found and
    changed, and the pipeline GitLab ran on the new commit.
    """
    rounds: list[dict[str, Any]] = []
    for event in events:
        name = (event.get("actor"), event.get("event"))
        data = event.get("data") or {}
        if name == ("workflow", "round"):
            rounds.append({"number": data.get("number"), "pipeline": data.get("pipeline")})
            continue
        if not rounds:
            continue
        current = rounds[-1]
        if name == ("remediation_round", "errors"):
            current["pipeline_url"] = data.get("pipeline_url")
            current["errors"] = [
                {"job": job.get("job"), "url": job.get("url"), "lines": list(job.get("lines") or [])}
                for job in data.get("jobs") or []
            ]
        elif name == ("workflow", "round_result"):
            current.update(
                {key: data.get(key) for key in ("status", "cause", "fix", "files")}
            )
        elif name == ("workflow", "validation"):
            current["commit"] = {"sha": data.get("commit_sha"), "url": data.get("commit_url")}
            current["result"] = {
                "status": data.get("status"),
                "pipeline": data.get("pipeline_id"),
                "url": data.get("pipeline_url"),
            }
    return rounds


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


def published_merge_request(run_id: str, store: RunStore) -> str | None:
    folder = store.folder(run_id)
    if folder is None or not (folder / MERGE_REQUEST_FILE).is_file():
        return None
    return (folder / MERGE_REQUEST_FILE).read_text(encoding="utf-8")

