"""Tests of the visitor API used by the web interface: project address, token, run key, publish token."""

from __future__ import annotations

import importlib
import json
import sys
import threading
from pathlib import Path
from typing import Any

import pytest
from flask.testing import FlaskClient

import startup.services
from app_runs.run_store import RunStore
from app_runs.services import ApiServices
from app_runs.thread_launcher import ThreadLauncher
from connectors.errors.errors import ConnectorError
from connectors.gitlab.client import GitLabClient
from journal.run_journal import RunJournal
from workflow_analysis.toolbox import Toolbox


def fake_run(project: str, pipeline_id: int | None, dry_run: bool, journal: RunJournal) -> dict[str, Any]:
    """Write a small report and a patch, like a real run."""
    journal.write_text("fix.patch", "--- a/app.py\n+++ b/app.py\n")
    report: dict[str, Any] = {"project": project, "status": "dry_run_patch_ready", "dry_run": dry_run}
    journal.write_report(report)
    return report


@pytest.fixture
def services(tmp_path: Path) -> ApiServices:
    store = RunStore(tmp_path)
    return ApiServices(launcher=ThreadLauncher(fake_run, store), store=store, api_token="t", webhook_secret="s")


@pytest.fixture
def client(services: ApiServices, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FlaskClient:
    # app.py builds its services when it is imported: give it the fake ones, then import it again.
    monkeypatch.setattr(startup.services, "build_api_services", lambda: (services, Toolbox([], [])))
    monkeypatch.delitem(sys.modules, "app", raising=False)
    app_module = importlib.import_module("app")
    codes = tmp_path / "access-codes"
    codes.write_text("recruiter:good-code\n", encoding="utf-8")
    monkeypatch.setattr(sys.modules["web_support"], "ACCESS_CODES_FILE", str(codes))
    return app_module.app.test_client()


def wait_for_runs() -> None:
    for thread in threading.enumerate():
        if thread is not threading.current_thread() and thread.daemon:
            thread.join(5)


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("https://gitlab.com/group/app", ("https://gitlab.com", "group/app", None)),
        ("https://gitlab.com/group/sub/app/", ("https://gitlab.com", "group/sub/app", None)),
        ("https://gitlab.com/group/app.git", ("https://gitlab.com", "group/app", None)),
        ("https://gitlab.com/group/app/-/pipelines/2978771", ("https://gitlab.com", "group/app", 2978771)),
        ("https://gitlab.example.fr/team/app/-/tree/main", ("https://gitlab.example.fr", "team/app", None)),
    ],
)
def test_project_address_is_split(client: FlaskClient, address: str, expected: tuple[str, str, int | None]) -> None:
    assert sys.modules["app"].parse_project_url(address) == expected


@pytest.mark.parametrize("address", ["http://gitlab.com/group/app", "gitlab.com/group/app", "https://gitlab.com/app"])
def test_bad_project_address_is_refused(client: FlaskClient, address: str) -> None:
    response = client.post("/web/runs", json={"access_code": "good-code", "project_url": address})
    assert response.status_code == 400


def test_the_run_uses_the_visitor_token_and_only_the_key_reads_it(
    client: FlaskClient, services: ApiServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    used: list[tuple[str, str, bool]] = []

    def user_run_function(url: str, token: str, tools: list[Any]) -> Any:
        def run(project: str, pipeline_id: int | None, dry_run: bool, journal: RunJournal) -> dict[str, Any]:
            used.append((url, token, dry_run))
            return fake_run(project, pipeline_id, dry_run, journal)

        return run

    app_module = sys.modules["app"]
    monkeypatch.setattr(app_module, "build_user_run_function", user_run_function)
    monkeypatch.setattr(app_module.GitLabClient, "get_project", lambda self, project: {"id": 1})
    response = client.post(
        "/web/runs",
        json={"access_code": "good-code", "project_url": "https://gitlab.example/group/app", "gitlab_token": "t"},
    )
    assert response.status_code == 202
    answer = response.get_json()
    wait_for_runs()
    assert used == [("https://gitlab.example", "t", True)]
    assert answer["project"] == "group/app"
    run = client.get(f"/web/runs/{answer['run_id']}", headers={"X-Run-Key": answer["run_key"]}).get_json()
    assert run["run"]["status"] == "dry_run_patch_ready"
    assert client.get(f"/web/runs/{answer['run_id']}", headers={"X-Run-Key": "other"}).status_code == 404
    assert answer["run_key"] not in str(services.store.get_run(answer["run_id"]))


def test_the_gitlab_token_is_required(client: FlaskClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(self: Any, project: str) -> Any:
        raise ConnectorError("404 Not Found", 404)

    monkeypatch.setattr(sys.modules["app"].GitLabClient, "get_project", refuse)
    response = client.post("/web/runs", json={"access_code": "good-code", "project_url": "https://gitlab.com/g/app"})
    assert response.status_code == 400
    assert "A GitLab access token is required" in response.get_json()["message"]
    with_token = client.post(
        "/web/runs",
        json={"access_code": "good-code", "project_url": "https://gitlab.com/g/app", "gitlab_token": "bad"},
    )
    assert "HTTP 404" in with_token.get_json()["message"]


def test_access_code_is_still_required(client: FlaskClient) -> None:
    response = client.post("/web/runs", json={"access_code": "wrong", "project_url": "https://gitlab.com/g/app"})
    assert response.status_code == 403


def test_publish_needs_a_token(client: FlaskClient, monkeypatch: pytest.MonkeyPatch) -> None:
    app_module = sys.modules["app"]
    monkeypatch.setattr(app_module, "build_user_run_function", lambda url, token, tools: fake_run)
    monkeypatch.setattr(app_module.GitLabClient, "get_project", lambda self, project: {"id": 1})
    answer = client.post(
        "/web/runs", json={"access_code": "good-code", "project_url": "https://gitlab.com/g/app", "gitlab_token": "t"}
    ).get_json()
    wait_for_runs()
    response = client.post(f"/web/runs/{answer['run_id']}/publish", headers={"X-Run-Key": answer["run_key"]})
    assert response.status_code == 400
    assert "api scope" in response.get_json()["message"]


def test_client_sends_no_token_header_when_empty() -> None:
    assert "PRIVATE-TOKEN" not in GitLabClient("https://gitlab.com").session.headers
    assert GitLabClient("https://gitlab.com", "x").session.headers["PRIVATE-TOKEN"] == "x"


def test_web_run_shows_no_internal_detail(
    client: FlaskClient, services: ApiServices, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_module = sys.modules["app"]

    def run_with_tools(project: str, pipeline_id: int | None, dry_run: bool, journal: RunJournal) -> dict[str, Any]:
        journal.log("diagnosis", "tool_call", {"tool": "search_code", "arguments": {"query": "add"}})
        report: dict[str, Any] = {
            "project": project,
            "status": "dry_run_patch_ready",
            "policy": {"confidential_projects": ["secret/app"]},
            "usage": {"llm_calls": 7, "per_agent": {"diagnosis": 3}},
            "rounds": [{"diagnosis": {"root_cause": "add subtracts", "evidence": "line 9", "fix_plan": "use +"}}],
        }
        journal.write_report(report)
        return report

    monkeypatch.setattr(app_module, "build_user_run_function", lambda url, token, tools: run_with_tools)
    monkeypatch.setattr(app_module.GitLabClient, "get_project", lambda self, project: {"id": 1})
    answer = client.post(
        "/web/runs", json={"access_code": "good-code", "project_url": "https://gitlab.com/g/app", "gitlab_token": "t"}
    ).get_json()
    wait_for_runs()
    body = client.get(f"/web/runs/{answer['run_id']}", headers={"X-Run-Key": answer["run_key"]}).get_data(as_text=True)
    assert "add subtracts" in body
    for internal in ("search_code", "tool_call", "evidence", "fix_plan", "llm_calls", "per_agent", "confidential"):
        assert internal not in body


def start_with_number(client: FlaskClient, monkeypatch: pytest.MonkeyPatch, number: Any, pipeline: Any) -> Any:
    app_module = sys.modules["app"]
    monkeypatch.setattr(app_module.GitLabClient, "get_project", lambda self, project: {"id": 1})
    monkeypatch.setattr(app_module.GitLabClient, "get_pipeline", pipeline)
    return client.post(
        "/web/runs",
        json={
            "access_code": "good-code",
            "project_url": "https://gitlab.com/g/app",
            "gitlab_token": "t",
            "pipeline_id": number,
        },
    )


@pytest.mark.parametrize("number", ["abc", "12a", "-3", "0"])
def test_wrong_pipeline_number_is_refused_never_replaced(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch, number: str
) -> None:
    response = start_with_number(client, monkeypatch, number, lambda self, project, pipeline_id: {"status": "failed"})
    assert response.status_code == 400
    assert "pipeline number" in response.get_json()["message"]


def test_successful_pipeline_is_answered_at_once(client: FlaskClient, monkeypatch: pytest.MonkeyPatch) -> None:
    response = start_with_number(
        client, monkeypatch, "#3005959635", lambda self, project, pipeline_id: {"status": "success"}
    )
    assert response.status_code == 400
    assert "#3005959635 passed" in response.get_json()["message"]


def test_unknown_pipeline_is_named(client: FlaskClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(self: Any, project: str, pipeline_id: int) -> Any:
        raise ConnectorError("404 Not found", 404)

    response = start_with_number(client, monkeypatch, "42", missing)
    assert response.status_code == 400
    assert "#42 does not exist in the project g/app" in response.get_json()["message"]


def test_agent_failure_shows_only_a_known_limit(client: FlaskClient) -> None:
    view = sys.modules["app"].public_run(
        {"status": "diagnosis_failed", "rounds": [{"error": "Agent 'diagnosis' reached its turn limit (10)."}]}
    )
    assert "AGENT_MAX_TURNS" in str(view)
    other = sys.modules["app"].public_run(
        {"status": "diagnosis_failed", "rounds": [{"error": "KeyError 'x' in /app/src/agent_runner.py line 9"}]}
    )
    assert "agent_runner" not in str(other)


def test_api_is_closed_without_api_token(services: ApiServices, client: FlaskClient) -> None:
    services.api_token = ""
    assert client.get("/api/v1/tools").status_code == 503
    assert client.post("/api/v1/runs", json={"project": "group/app"}).status_code == 503
    # The visitor API does not use API_TOKEN, and /health, / and /web/config stay public
    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 200
    assert client.get("/web/config").status_code == 200


def test_api_needs_the_right_bearer_token(client: FlaskClient) -> None:
    assert client.get("/api/v1/tools").status_code == 401
    assert client.get("/api/v1/tools", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/v1/tools", headers={"Authorization": "Bearer t"}).status_code == 200


def test_a_web_run_can_be_followed_again_after_a_restart(tmp_path: Path) -> None:
    from web_support import WebOwners

    first = WebOwners()
    first.runs_dir = tmp_path
    first["20261103-174922-dc497d"] = ("https://gitlab.com", "hash-of-the-key")
    # A new process starts with an empty memory and reads the owner from the run's folder
    restarted = WebOwners()
    restarted.runs_dir = tmp_path
    assert restarted.get("20261103-174922-dc497d") == ("https://gitlab.com", "hash-of-the-key")
    assert restarted.get("unknown-run") is None
    assert restarted.get("../etc") is None
    # Only the hash of the run key is written, never the key itself
    saved = json.loads((tmp_path / "20261103-174922-dc497d" / "web_owner.json").read_text())
    assert set(saved) == {"gitlab_url", "key_hash"}


def test_a_visitor_gets_a_clear_message_after_the_daily_limit(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_module = sys.modules["app"]
    monkeypatch.setattr(app_module, "build_user_run_function", lambda url, token, tools: fake_run)
    monkeypatch.setattr(app_module.GitLabClient, "get_project", lambda self, project: {"id": 1})
    monkeypatch.setattr(app_module.daily_limit, "limit", 1)
    body = {"access_code": "good-code", "project_url": "https://gitlab.com/group/app", "gitlab_token": "t"}
    assert client.post("/web/runs", json=body).status_code == 202
    wait_for_runs()
    refused = client.post("/web/runs", json=body)
    assert refused.status_code == 429
    assert "Daily limit reached: 1 analyses per access code" in refused.get_json()["message"]


def test_the_config_gives_the_access_request_address_only_when_it_is_valid(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import web_support

    assert client.get("/web/config").get_json()["access_request_email"] == ""
    monkeypatch.setattr(web_support, "ACCESS_REQUEST_EMAIL", "owner@example.com")
    assert client.get("/web/config").get_json()["access_request_email"] == "owner@example.com"
    assert web_support.access_request_email("not an address") == ""
    assert web_support.access_request_email('a@b.com" onclick="x') == ""
