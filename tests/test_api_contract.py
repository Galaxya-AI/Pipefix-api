"""The API keeps its contract (openapi.yaml): every route is described, and the visitor API answers
match the schemas the web interface relies on. CORS opens only the visitor API to the listed origins."""

from __future__ import annotations

import re
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from flask import Flask, Response
from flask.testing import FlaskClient
from jsonschema import Draft202012Validator

from journal.run_journal import RunJournal
from test_visitor_api import client, fake_run, services, wait_for_runs  # noqa: F401  (pytest fixtures)
from test_visitor_fix_until_green import PIPELINE, fake_loop
from visitor_api.cors import add_cors_headers, allowed_origins

SPEC: dict[str, Any] = yaml.safe_load(Path(__file__).parent.parent.joinpath("openapi.yaml").read_text("utf-8"))
METHODS = {"get", "post", "put", "patch", "delete"}


def check(instance: Any, schema_name: str) -> None:
    """Validate an answer against a schema of openapi.yaml (OpenAPI 3.1 schemas are JSON Schema 2020-12)."""
    schema = {"$ref": f"#/components/schemas/{schema_name}", "components": SPEC["components"]}
    Draft202012Validator(schema).validate(instance)


def spec_operations() -> set[tuple[str, str]]:
    return {(method.upper(), path) for path, item in SPEC["paths"].items() for method in item if method in METHODS}


def test_the_description_is_a_valid_openapi_document() -> None:
    assert SPEC["openapi"].startswith("3.1")
    # Every $ref points to something that exists
    text = yaml.safe_dump(SPEC)
    for ref in set(re.findall(r"#/components/(\w+)/(\w+)", text)):
        assert ref[1] in SPEC["components"][ref[0]], ref
    for schema in SPEC["components"]["schemas"].values():
        Draft202012Validator.check_schema(schema)


def test_every_route_of_the_application_is_described_and_nothing_else(client: FlaskClient) -> None:  # noqa: F811
    app: Flask = sys.modules["app"].app
    routes = {
        (method, re.sub(r"<(?:\w+:)?(\w+)>", r"{\1}", rule.rule))
        for rule in app.url_map.iter_rules()
        if rule.endpoint != "static"
        for method in (rule.methods or set()) - {"HEAD", "OPTIONS"}
    }
    assert routes == spec_operations()


def start(client: FlaskClient, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:  # noqa: F811
    app_module = sys.modules["app"]
    monkeypatch.setattr(app_module, "build_user_run_function", lambda url, token, tools: fake_run)
    monkeypatch.setattr(app_module.GitLabClient, "get_project", lambda self, project: {"id": 1})
    body = {"access_code": "good-code", "project_url": "https://gitlab.com/g/app", "gitlab_token": "t"}
    response = client.post("/web/runs", json=body)
    assert response.status_code == 202
    answer: dict[str, Any] = response.get_json()
    check(answer, "WebRunStarted")
    wait_for_runs()
    return answer


def test_service_answers_match_the_contract(client: FlaskClient) -> None:  # noqa: F811
    check(client.get("/").get_json(), "RootInfo")
    check(client.get("/health").get_json(), "Health")
    check(client.get("/web/config").get_json(), "WebConfig")
    described = client.get("/api/openapi.yaml")
    assert described.status_code == 200
    assert yaml.safe_load(described.get_data(as_text=True)) == SPEC


def test_visitor_answers_match_the_contract(client: FlaskClient, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    answer = start(client, monkeypatch)
    headers = {"X-Run-Key": answer["run_key"]}
    view = client.get(f"/web/runs/{answer['run_id']}", headers=headers).get_json()
    check(view, "WebRunView")
    assert view["run"]["status"] == "dry_run_patch_ready"

    app_module = sys.modules["app"]
    monkeypatch.setattr(app_module, "load_saved_fix", lambda journal, report: (PIPELINE, SimpleNamespace(), "b"))
    monkeypatch.setattr(app_module, "build_user_loop_function", lambda *args, **kwargs: fake_loop)
    loop = client.post(
        f"/web/runs/{answer['run_id']}/fix-until-green", headers={**headers, "X-Gitlab-Token": "api"}
    ).get_json()
    check(loop, "LoopStarted")
    wait_for_runs()
    loop_view = client.get(f"/web/runs/{loop['run_id']}", headers=headers).get_json()
    check(loop_view, "WebRunView")
    assert len(loop_view["rounds"]) == 2


def test_a_run_in_progress_matches_the_contract(
    client: FlaskClient,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()

    def slow_run(project: str, pipeline_id: int | None, dry_run: bool, journal: RunJournal) -> dict[str, Any]:
        journal.log("triage", "start", {})
        release.wait(5)
        return fake_run(project, pipeline_id, dry_run, journal)

    app_module = sys.modules["app"]
    monkeypatch.setattr(app_module, "build_user_run_function", lambda url, token, tools: slow_run)
    monkeypatch.setattr(app_module.GitLabClient, "get_project", lambda self, project: {"id": 1})
    body = {"access_code": "good-code", "project_url": "https://gitlab.com/g/app", "gitlab_token": "t"}
    answer = client.post("/web/runs", json=body).get_json()
    headers = {"X-Run-Key": answer["run_key"]}
    view = client.get(f"/web/runs/{answer['run_id']}", headers=headers).get_json()
    release.set()
    wait_for_runs()
    check(view, "WebRunView")
    assert view["patch"] is None
    assert view["run"] is None or view["run"]["status"] in ("queued", "running")


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"access_code": "wrong", "project_url": "https://gitlab.com/g/app", "gitlab_token": "t"}, 403),
        ({"access_code": "good-code", "project_url": "", "gitlab_token": "t"}, 400),
        ({"access_code": "good-code", "project_url": "https://gitlab.com/g/app"}, 400),
    ],
)
def test_errors_match_the_contract(client: FlaskClient, body: dict[str, Any], status: int) -> None:  # noqa: F811
    response = client.post("/web/runs", json=body)
    assert response.status_code == status
    check(response.get_json(), "Error")
    # The status is one the description lists for this route
    assert str(status) in SPEC["paths"]["/web/runs"]["post"]["responses"]


def test_unknown_run_answers_a_contract_error(client: FlaskClient) -> None:  # noqa: F811
    response = client.get("/web/runs/nope", headers={"X-Run-Key": "x"})
    assert response.status_code == 404
    check(response.get_json(), "Error")


# --- CORS ---

ORIGIN = "https://pipefix.example"


def cors_client(client: FlaskClient, monkeypatch: pytest.MonkeyPatch) -> FlaskClient:  # noqa: F811
    app_module = sys.modules["app"]
    monkeypatch.setattr(
        app_module,
        "add_cors_headers",
        lambda request, response: add_cors_headers(
            request, response, allowed_origins(f"{ORIGIN}/, http://localhost:5173")
        ),
    )
    return client


def test_origins_are_read_from_a_comma_separated_list() -> None:
    assert allowed_origins(" https://a.example/ ,, http://localhost:5173 ") == {
        "https://a.example",
        "http://localhost:5173",
    }
    assert allowed_origins("") == frozenset()


def test_a_listed_origin_may_call_the_visitor_api(client: FlaskClient, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    cors = cors_client(client, monkeypatch)
    preflight = cors.options(
        "/web/runs",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["Access-Control-Allow-Origin"] == ORIGIN
    assert "X-Run-Key" in preflight.headers["Access-Control-Allow-Headers"]
    assert "POST" in preflight.headers["Access-Control-Allow-Methods"]
    # Error answers carry the header too, so the interface can show the message
    refused = cors.post("/web/runs", json={"access_code": "wrong"}, headers={"Origin": ORIGIN})
    assert refused.status_code == 403
    assert refused.headers["Access-Control-Allow-Origin"] == ORIGIN
    assert "Origin" in refused.headers.get("Vary", "")
    config = cors.get("/web/config", headers={"Origin": "http://localhost:5173"})
    assert config.headers["Access-Control-Allow-Origin"] == "http://localhost:5173"


def test_other_origins_and_the_server_api_get_no_cors_header(
    client: FlaskClient,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cors = cors_client(client, monkeypatch)
    other = cors.get("/web/config", headers={"Origin": "https://evil.example"})
    assert "Access-Control-Allow-Origin" not in other.headers
    tools = cors.get("/api/v1/tools", headers={"Origin": ORIGIN, "Authorization": "Bearer t"})
    assert tools.status_code == 200
    assert "Access-Control-Allow-Origin" not in tools.headers


def test_without_a_list_only_the_same_domain_may_call(client: FlaskClient) -> None:  # noqa: F811
    assert "Access-Control-Allow-Origin" not in client.get("/web/config", headers={"Origin": ORIGIN}).headers


def test_cors_helper_leaves_requests_without_origin_alone() -> None:
    app = Flask(__name__)
    with app.test_request_context("/web/config"):
        from flask import request

        response = add_cors_headers(request, Response("{}"), frozenset({ORIGIN}))
    assert "Access-Control-Allow-Origin" not in response.headers


def test_the_open_merge_request_of_an_earlier_fix_is_shown_and_crashes_are_not_detailed() -> None:
    from visitor_api.views import UNEXPECTED_ERROR, public_run

    view = public_run({"status": "dry_run_patch_ready", "previous_merge_request_url": "https://mr/3", "rounds": []})
    assert view is not None and view["merge_request_url"] == "https://mr/3"
    check(view, "PublicRun")
    crashed = public_run({"status": "error", "error": "KeyError: 'id' in /app/src/x.py line 12"})
    assert crashed is not None and crashed["error"] == UNEXPECTED_ERROR
    known = public_run({"status": "pipeline_timeout", "error": "GitLab did not finish the pipeline in 900 s."})
    assert known is not None and known["error"] == "GitLab did not finish the pipeline in 900 s."


def test_open_paths_always_vary_on_the_origin(client: FlaskClient) -> None:  # noqa: F811
    assert "Origin" in client.get("/api/openapi.yaml").headers.get("Vary", "")
    assert "Origin" in client.get("/web/config", headers={"Origin": "https://other.example"}).headers.get("Vary", "")
