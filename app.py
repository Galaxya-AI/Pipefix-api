"""REST API and web page (PipeFix) of the CI remediation system: the Flask application and all its routes.

Development: python app.py
Production:  gunicorn app:app (see docker-compose.yml)

Routes:
- GET  /health                               liveness check
- GET  /api/v1/tools                         available tools
- POST /api/v1/runs                          start a run -> 202 {"run_id": ...}
- GET  /api/v1/runs                          list the runs
- GET  /api/v1/runs/<run_id>                 status or report of a run
- GET  /api/v1/runs/<run_id>/events          journal events of a run
- GET  /api/v1/runs/<run_id>/patch           proposed patch (text/x-diff)
- POST /api/v1/webhooks/gitlab               GitLab "Pipeline Hook" -> 202
- GET  /                                     PipeFix web page
- POST /web/runs                             dry run on the visitor's project -> 202 {"run_id": ..., "run_key": ...}
- GET  /web/runs/<run_id>                    status, agents and patch of a web run (X-Run-Key header)
- POST /web/runs/<run_id>/publish            publish that fix as a draft merge request (X-Run-Key + X-Gitlab-Token)
"""

from __future__ import annotations

import atexit
import hashlib
import hmac
import os
import re
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from flask import Flask, Response, abort, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from app_runs.launcher import LaunchError
from app_runs.thread_launcher import ThreadLauncher
from connectors.errors.errors import ConnectorError
from connectors.gitlab.client import GitLabClient
from journal.run_journal import RunJournal
from startup.services import build_api_services, build_user_run_function
from workflow_publication.publisher import MERGE_REQUEST_FILE, publish_saved_fix

API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "8000"))

# Pipelines of these branches are ignored: they are the system's own fix branches
FIX_BRANCH_PREFIX = os.getenv("FIX_BRANCH_PREFIX", "auto-fix/")

# One "name:code" per line, given by the owner to each visitor of the web page. In OpenShift it is a mounted
# secret: edited in the console, the file changes without a restart, so it is read again at each new run.
ACCESS_CODES_FILE = os.getenv("ACCESS_CODES_FILE", "/etc/pipefix/access-codes")

app = Flask(__name__)

services, toolbox = build_api_services()
# Stops the MCP servers when the process stops.
atexit.register(toolbox.close)


@app.errorhandler(HTTPException)
def http_error(error: HTTPException) -> tuple[Response, int]:
    """Return HTTP errors as JSON instead of HTML."""
    return jsonify({"error": error.name, "message": error.description}), error.code or 500


@app.before_request
def require_api_token() -> None:
    """Abort with 401 if API_TOKEN is set and the Bearer token does not match."""
    # /health is public and the webhook checks its own secret.
    if not request.path.startswith("/api/v1/") or request.path.startswith("/api/v1/webhooks/"):
        return
    if not services.api_token:
        return
    header = request.headers.get("Authorization", "")
    given = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
    if not hmac.compare_digest(given, services.api_token):
        abort(401, description="Missing or invalid Bearer token.")


@app.get("/health")
def health() -> Response:
    return jsonify({"status": "ok"})


@app.get("/api/v1/tools")
def list_tools() -> Response:
    return jsonify(
        {
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "tags": sorted(tool.tags),
                    "read_only": tool.read_only,
                }
                for tool in services.tools
            ]
        }
    )


@app.post("/api/v1/runs")
def create_run() -> tuple[Response, int]:
    """Start a remediation run.

    JSON body: {"project": "group/app", "pipeline_id": 42, "apply": false}.
    "pipeline_id" is optional (latest failed pipeline). "apply": true publishes
    the fix as a draft merge request; false (default) only writes the patch.
    Answers 202 with the run id, 422 if there is no failed pipeline, 502 if GitLab cannot be reached.
    """
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        abort(400, description="JSON object expected.")
    project = str(body.get("project", "")).strip()
    if not project:
        abort(400, description='"project" is required.')
    pipeline_id = body.get("pipeline_id")
    if pipeline_id is not None and (not isinstance(pipeline_id, int) or pipeline_id < 1):
        abort(400, description='"pipeline_id" must be a positive integer.')
    apply = body.get("apply", False)
    if not isinstance(apply, bool):
        abort(400, description='"apply" must be true or false.')
    try:
        # The id of the same run if it is already queued.
        run_id = services.launcher.launch(project, pipeline_id, dry_run=not apply)
    except LaunchError as error:
        abort(422, description=str(error))
    except ConnectorError as error:
        abort(502, description=f"GitLab error: {error}")
    # The run goes on in the background; its status says "queued" or "running".
    return jsonify({"run_id": run_id, "status_url": f"/api/v1/runs/{run_id}"}), 202


@app.get("/api/v1/runs")
def list_runs() -> Response:
    return jsonify({"runs": services.store.list_runs()})


@app.get("/api/v1/runs/<run_id>")
def get_run(run_id: str) -> Response:
    """Return the status of a running run, or the report of a finished one."""
    run = services.store.get_run(run_id)
    if run is None:
        abort(404, description=f"Unknown run: {run_id}")
    return jsonify(run)


@app.get("/api/v1/runs/<run_id>/events")
def get_run_events(run_id: str) -> Response:
    events = services.store.events(run_id)
    if events is None:
        abort(404, description=f"Unknown run: {run_id}")
    return jsonify({"run_id": run_id, "events": events})


@app.get("/api/v1/runs/<run_id>/patch")
def get_run_patch(run_id: str) -> Response:
    """Return the patch proposed by a dry run, as text/x-diff."""
    patch = services.store.patch(run_id)
    if patch is None:
        abort(404, description=f"No patch for run: {run_id}")
    return Response(patch, mimetype="text/x-diff")


@app.post("/api/v1/webhooks/gitlab")
def gitlab_webhook() -> tuple[Response, int]:
    """Start a run when GitLab reports a failed pipeline; other events get 202 "ignored".

    In GitLab: Settings > Webhooks, URL <api>/api/v1/webhooks/gitlab, secret token
    = GITLAB_WEBHOOK_SECRET, trigger "Pipeline events".
    """
    if not services.webhook_secret:
        abort(503, description="Webhook disabled: set GITLAB_WEBHOOK_SECRET.")
    if not hmac.compare_digest(request.headers.get("X-Gitlab-Token", ""), services.webhook_secret):
        abort(401, description="Invalid X-Gitlab-Token.")
    event = request.get_json(silent=True)
    if not isinstance(event, dict):
        abort(400, description="JSON object expected.")
    attributes: dict[str, Any] = event.get("object_attributes") or {}
    reason = ""
    if event.get("object_kind") != "pipeline":
        reason = "not a pipeline event"
    elif attributes.get("status") != "failed":
        reason = f"pipeline status is {attributes.get('status')}"
    # Pipelines of fix branches are ignored, to avoid loops.
    elif str(attributes.get("ref", "")).startswith(FIX_BRANCH_PREFIX):
        reason = "fix branch"
    if reason:
        return jsonify({"status": "ignored", "reason": reason}), 202
    project = str((event.get("project") or {}).get("path_with_namespace", ""))
    pipeline_id = int(attributes.get("id", 0))
    if not project or pipeline_id < 1:
        abort(400, description="Project or pipeline id missing.")
    try:
        # The id of the same run if it is already queued.
        run_id = services.launcher.launch(project, pipeline_id, dry_run=services.default_dry_run)
    # E.g. the pipeline was retried and passed meanwhile.
    except LaunchError as error:
        return jsonify({"status": "ignored", "reason": str(error)}), 202
    # GitLab retries the webhook later.
    except ConnectorError as error:
        abort(502, description=f"GitLab error: {error}")
    return jsonify({"status": "started", "run_id": run_id}), 202


# Run id -> GitLab URL and hash of the run key given to the visitor who started it: only that visitor can read or
# publish the run. Neither the key nor the GitLab token is stored. Kept in memory: web runs use one process.
web_owners: dict[str, tuple[str, str]] = {}

# Project address pasted from the browser: https://<host>/<group>/<subgroups...>/<project>, optionally followed by
# ".git" or a GitLab page such as "/-/pipelines/123" (the pipeline number is then taken from it).
PIPELINE_IN_URL = re.compile(r"/-/pipelines/(\d+)")

# The agents of a run, in order, as the web page shows them.
STAGES = ("triage", "diagnosis", "fixer", "reviewer")


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


@app.get("/")
def index() -> str:
    return render_template("index.html")


@app.post("/web/runs")
def create_web_run() -> tuple[Response, int]:
    """Start a dry run on the visitor's project; nothing is published.

    JSON body: {"access_code", "project_url", "gitlab_token" (optional), "pipeline_id" (optional)}.
    The token is optional: a public project whose pipelines are public is read anonymously. It is used for this
    run only and never stored. The answer carries a run key the page sends back to read or publish the run.
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        abort(400, description="JSON object expected.")
    visitor = access_owner(str(body.get("access_code", "")).strip())
    if visitor is None:
        abort(403, description="Unknown access code. Ask the owner of PipeFix for one.")
    gitlab_token = str(body.get("gitlab_token", "")).strip()
    address = str(body.get("project_url", "")).strip()
    if not address:
        abort(400, description="The project address is required, for example https://gitlab.com/group/project.")
    try:
        gitlab_url, project, pipeline_in_url = parse_project_url(address)
    except ValueError as error:
        abort(400, description=str(error))
    pipeline_id = body.get("pipeline_id") or pipeline_in_url
    if pipeline_id is not None and (not isinstance(pipeline_id, int) or pipeline_id < 1):
        abort(400, description="The pipeline number must be a positive number.")
    try:
        GitLabClient(gitlab_url, gitlab_token).get_project(project)
    except ConnectorError as error:
        if gitlab_token:
            abort(400, description=f"GitLab refused the token or the project (HTTP {error.status_code or 'error'}).")
        abort(
            400,
            description="This project cannot be read without a token: it is private or does not exist. "
            "Add an access token with the read_api scope.",
        )
    launcher = ThreadLauncher(build_user_run_function(gitlab_url, gitlab_token, services.tools), services.store)
    run_id = launcher.launch(project, pipeline_id, dry_run=True)
    run_key = secrets.token_urlsafe(32)
    web_owners[run_id] = (gitlab_url, secret_hash(run_key))
    # The name only, never the code.
    RunJournal(services.store.runs_dir, run_id, verbose=False).log("web", "visitor", {"name": visitor})
    return jsonify({"run_id": run_id, "run_key": run_key, "project": project}), 202


@app.get("/web/runs/<run_id>")
def get_web_run(run_id: str) -> Response:
    """Status (then report), state of each agent, last tool calls and patch of a web run."""
    web_owner(run_id)
    run = services.store.get_run(run_id)
    finished = run is not None and run.get("status") not in ("queued", "running")
    events = services.store.events(run_id) or []
    calls = [
        {"agent": event.get("actor"), "tool": (event.get("data") or {}).get("tool")}
        for event in events
        if event.get("event") == "tool_call"
    ]
    return jsonify(
        {
            "run": run,
            "stages": stage_states(events, finished),
            "calls": calls[-6:],
            "patch": services.store.patch(run_id),
            "merge_request": published_merge_request(run_id),
        }
    )


def published_merge_request(run_id: str) -> str | None:
    folder = services.store.folder(run_id)
    if folder is None or not (folder / MERGE_REQUEST_FILE).is_file():
        return None
    return (folder / MERGE_REQUEST_FILE).read_text(encoding="utf-8")


@app.post("/web/runs/<run_id>/publish")
def publish_web_run(run_id: str) -> Response:
    """Publish the fix shown to the visitor as a draft merge request; needs a token with the api scope."""
    gitlab_url = web_owner(run_id)
    gitlab_token = request.headers.get("X-Gitlab-Token", "").strip()
    if not gitlab_token:
        abort(400, description="Creating the merge request needs an access token with the api scope.")
    report = services.store.read_report(run_id)
    if report is None or report.get("status") != "dry_run_patch_ready":
        abort(409, description="This run has no fix to publish.")
    try:
        url = publish_saved_fix(
            GitLabClient(gitlab_url, gitlab_token), RunJournal(services.store.runs_dir, run_id, verbose=False), report
        )
    except ConnectorError as error:
        abort(
            502,
            description=f"GitLab refused the merge request (HTTP {error.status_code or 'error'}): "
            "the token needs the api scope and the Developer role on the project.",
        )
    return jsonify({"merge_request": url})


if __name__ == "__main__":
    app.run(host=API_HOST, port=API_PORT)
