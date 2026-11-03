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
- POST /web/runs/<run_id>/fix-until-green    publish that fix, then keep fixing until the pipeline passes;
                                             answers a new run id, followed with the same run key
"""

from __future__ import annotations

import atexit
import hmac
import os
import secrets
from typing import Any

from flask import Flask, Response, abort, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from app_runs.daily_limit import daily_limit
from app_runs.launcher import LaunchError
from app_runs.thread_launcher import ThreadLauncher
from connectors.errors.errors import ConnectorError
from connectors.gitlab.client import GitLabClient
from journal.run_journal import RunJournal
from startup.services import build_api_services, build_user_loop_function, build_user_run_function
from web_support import (
    access_owner,
    check_pipeline_to_fix,
    parse_project_url,
    public_run,
    published_merge_request,
    read_pipeline_number,
    round_views,
    secret_hash,
    stage_states,
    web_owner,
    web_owners,
)
from workflow_publication.publisher import MERGE_REQUEST_FILE, load_saved_fix, publish_saved_fix, saved_start_pipeline

API_HOST = os.getenv("API_HOST", "127.0.0.1")
API_PORT = int(os.getenv("API_PORT", "8000"))

# Written in a web run folder once its fix went into the loop: the id of the loop run
LOOP_RUN_FILE = "loop_run.id"

# Pipelines of these branches are ignored: they are the system's own fix branches
FIX_BRANCH_PREFIX = os.getenv("FIX_BRANCH_PREFIX", "auto-fix/")


app = Flask(__name__)

services, toolbox = build_api_services()
# Web runs can be followed again after a restart when the runs folder is a persistent volume
web_owners.runs_dir = services.store.runs_dir
daily_limit.runs_dir = services.store.runs_dir
# Stops the MCP servers when the process stops.
atexit.register(toolbox.close)


@app.errorhandler(HTTPException)
def http_error(error: HTTPException) -> tuple[Response, int]:
    """Return HTTP errors as JSON instead of HTML."""
    return jsonify({"error": error.name, "message": error.description}), error.code or 500


@app.errorhandler(Exception)
def unexpected_error(error: Exception) -> tuple[Response, int]:
    """Never return a Flask HTML traceback to callers expecting API JSON."""
    app.logger.exception("Unhandled request failure", exc_info=error)
    if request.path.startswith(("/api/", "/web/")):
        return jsonify({"error": "Internal Server Error", "message": "Request failed; consult server logs."}), 500
    return jsonify({"error": "Internal Server Error", "message": "Request failed."}), 500


@app.before_request
def require_api_token() -> None:
    """Abort with 503 if API_TOKEN is empty, 401 if the Bearer token does not match.

    The API runs with the server's GitLab token and LLM key: without API_TOKEN it stays closed,
    so a forgotten setting never opens it. The web page uses access codes, not API_TOKEN.
    """
    # /health is public and the webhook checks its own secret.
    if not request.path.startswith("/api/v1/") or request.path.startswith("/api/v1/webhooks/"):
        return
    if not services.api_token:
        abort(503, description="API disabled: set API_TOKEN.")
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
    pipeline_id = read_pipeline_number(body.get("pipeline_id")) or pipeline_in_url
    gitlab = GitLabClient(gitlab_url, gitlab_token)
    try:
        gitlab.get_project(project)
    except ConnectorError as error:
        if gitlab_token:
            abort(400, description=f"GitLab refused the token or the project (HTTP {error.status_code or 'error'}).")
        abort(
            400,
            description="This project cannot be read without a token: it is private or does not exist. "
            "Add an access token with the read_api scope.",
        )
    if pipeline_id is not None:
        check_pipeline_to_fix(gitlab, project, pipeline_id)
    if not daily_limit.take(visitor):
        abort(
            429,
            description=f"Daily limit reached: {daily_limit.limit} analyses per access code. Try again tomorrow.",
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
    """Public report, state of each agent and patch of a web run."""
    web_owner(run_id)
    run = services.store.get_run(run_id)
    finished = run is not None and run.get("status") not in ("queued", "running")
    events = services.store.events(run_id) or []
    return jsonify(
        {
            "run": public_run(run),
            "stages": stage_states(events, finished),
            "rounds": round_views(events),
            "patch": services.store.patch(run_id),
            "merge_request": published_merge_request(run_id, services.store),
        }
    )


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


@app.post("/web/runs/<run_id>/fix-until-green")
def fix_web_run_until_green(run_id: str) -> Response:
    """Publish the fix shown to the visitor, then keep fixing until the pipeline passes (needs the api scope).

    The loop runs in the background as a new run; the page follows it with the same run key.
    The draft merge request opens only when GitLab reports a green pipeline.
    """
    gitlab_url = web_owner(run_id)
    gitlab_token = request.headers.get("X-Gitlab-Token", "").strip()
    if not gitlab_token:
        abort(400, description="Fixing until the pipeline passes needs an access token with the api scope.")
    folder = services.store.folder(run_id)
    if folder is None:
        abort(404, description=f"Unknown run: {run_id}")
    # Asked twice (double click, page reloaded): the same loop, never a second one.
    if (folder / LOOP_RUN_FILE).is_file():
        return jsonify({"run_id": (folder / LOOP_RUN_FILE).read_text(encoding="utf-8").strip()})
    if (folder / MERGE_REQUEST_FILE).is_file():
        abort(409, description="This fix was already published as a merge request.")
    report = services.store.read_report(run_id)
    if report is None or report.get("status") != "dry_run_patch_ready":
        abort(409, description="This run has no fix to publish.")
    journal = RunJournal(services.store.runs_dir, run_id, verbose=False)
    pipeline, first_result, branch = load_saved_fix(journal, report)
    project = str(report["project"])
    client = GitLabClient(gitlab_url, gitlab_token)
    try:
        # Checked now, so a wrong token is reported here and not after minutes of work.
        client.get_project(project)
    except ConnectorError as error:
        abort(400, description=f"GitLab refused the token or the project (HTTP {error.status_code or 'error'}).")
    start = saved_start_pipeline(journal)
    run_function = build_user_loop_function(
        gitlab_url, gitlab_token, services.tools, pipeline, first_result, branch, start
    )
    launcher = ThreadLauncher(run_function, services.store)
    loop_id = launcher.launch(project, int(pipeline["id"]), dry_run=False)
    # The visitor follows the loop with the key of the analysis.
    web_owners[loop_id] = web_owners[run_id]
    journal.write_text(LOOP_RUN_FILE, loop_id)
    journal.log("web", "loop_started", {"loop_run": loop_id})
    return jsonify({"run_id": loop_id})


if __name__ == "__main__":
    app.run(host=API_HOST, port=API_PORT)
