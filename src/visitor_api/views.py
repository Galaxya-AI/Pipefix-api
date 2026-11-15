"""What the web interface may see of a run: the agents' progress, the result and the rounds.

Never the tools the agents called, their prompts, the usage or the settings.
"""

from __future__ import annotations

import re
from typing import Any

from app_runs.run_store import RunStore
from workflow_publication.publisher import MERGE_REQUEST_FILE

STAGES = ("triage", "diagnosis", "fixer", "reviewer")
UNEXPECTED_ERROR = (
    "The analysis stopped on an unexpected error. Try again; if it happens again, tell the owner of PipeFix."
)


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
    """Only what the interface shows: never the tools the agents called, their prompts, usage or settings."""
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
    keys = ("status", "project", "error", "merge_request", "fix_branch")
    view = {key: run.get(key) for key in keys if run.get(key)}
    # An open merge request of an earlier fix for the same pipeline
    if run.get("previous_merge_request_url"):
        view["merge_request_url"] = run["previous_merge_request_url"]
    # A crash: its text (exception, paths) stays in the logs; the visitor gets a sentence to act on
    if run.get("status") == "error" and view.get("error"):
        view["error"] = UNEXPECTED_ERROR
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
            current.update({key: data.get(key) for key in ("status", "cause", "fix", "files")})
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
