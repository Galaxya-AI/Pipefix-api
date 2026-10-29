"""Tests of the job log read without a token on a public project."""

from __future__ import annotations

from typing import Any

import pytest

from connectors.errors.errors import ConnectorError
from connectors.gitlab.client import GitLabClient


class FakeResponse:
    def __init__(self, status: int, text: str = "", content_type: str = "text/plain") -> None:
        self.status_code = status
        self.text = text
        self.content = text.encode()
        self.encoding = "utf-8"
        self.headers = {"Content-Type": content_type}

    def json(self) -> Any:
        return {"path_with_namespace": "group/app"}


def install(client: GitLabClient, pages: dict[str, FakeResponse], seen: list[str]) -> None:
    def answer(method: str, url: str, **kwargs: Any) -> FakeResponse:
        seen.append(url)
        return pages[url]

    client.session.request = answer  # type: ignore[method-assign]
    client.session.get = lambda url, **kwargs: answer("GET", url)  # type: ignore[method-assign]


API_TRACE = "https://gitlab.com/api/v4/projects/group%2Fapp/jobs/7/trace"
RAW_LOG = "https://gitlab.com/group/app/-/jobs/7/raw"


def test_without_token_the_public_log_page_is_read() -> None:
    client, seen = GitLabClient("https://gitlab.com"), []
    install(
        client,
        {API_TRACE: FakeResponse(401, '{"message":"401 Unauthorized"}'), RAW_LOG: FakeResponse(200, "boom")},
        seen,
    )
    assert client.job_trace("group/app", 7) == "boom"
    assert seen == [API_TRACE, RAW_LOG]


def test_numeric_project_id_is_turned_into_its_path() -> None:
    client, seen = GitLabClient("https://gitlab.com"), []
    api = "https://gitlab.com/api/v4/projects/42/jobs/7/trace"
    install(
        client,
        {
            api: FakeResponse(403),
            "https://gitlab.com/api/v4/projects/42": FakeResponse(200),
            RAW_LOG: FakeResponse(200, "log"),
        },
        seen,
    )
    assert client.job_trace(42, 7) == "log"


@pytest.mark.parametrize("page", [FakeResponse(302), FakeResponse(404), FakeResponse(200, "<html>", "text/html")])
def test_private_log_gives_a_clear_message(page: FakeResponse) -> None:
    client = GitLabClient("https://gitlab.com")
    install(client, {API_TRACE: FakeResponse(401), RAW_LOG: page}, [])
    with pytest.raises(ConnectorError) as error:
        client.job_trace("group/app", 7)
    if page.status_code != 404:
        assert "read_api" in str(error.value)


def test_with_token_the_api_error_is_kept() -> None:
    client, seen = GitLabClient("https://gitlab.com", "glpat-x"), []
    install(client, {API_TRACE: FakeResponse(401)}, seen)
    with pytest.raises(ConnectorError):
        client.job_trace("group/app", 7)
    assert seen == [API_TRACE]


def test_api_log_is_used_when_anonymous_access_is_allowed() -> None:
    client, seen = GitLabClient("https://gitlab.example"), []
    api = "https://gitlab.example/api/v4/projects/group%2Fapp/jobs/7/trace"
    install(client, {api: FakeResponse(200, "api log")}, seen)
    assert client.job_trace("group/app", 7) == "api log"
    assert seen == [api]
