import json
import sys
from pathlib import Path

import pytest

from mcp_runtime.client import server_environment
from mcp_runtime.config import load_mcp_servers
from workflow_analysis.toolbox import build_toolbox


def write(tmp_path: Path, servers: object) -> str:
    path = tmp_path / "mcp_servers.json"
    path.write_text(json.dumps(servers), encoding="utf-8")
    return str(path)


def test_missing_default_file_means_no_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert load_mcp_servers("") == {}
    assert build_toolbox([], "").tools == []


def test_missing_named_file_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="does not exist"):
        load_mcp_servers(str(tmp_path / "absent.json"))


def test_variables_are_replaced_from_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITLAB_TOKEN", "abc")
    path = write(tmp_path, {"gitlab": {"command": ["gitlab-mcp"], "env": {"TOKEN": "${GITLAB_TOKEN}"}}})
    assert load_mcp_servers(path)["gitlab"]["env"] == {"TOKEN": "abc"}


def test_missing_variable_names_it_without_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    path = write(tmp_path, {"gitlab": {"command": ["gitlab-mcp"], "env": {"TOKEN": "${GITLAB_TOKEN}"}}})
    with pytest.raises(ValueError, match="GITLAB_TOKEN is not set"):
        load_mcp_servers(path)


@pytest.mark.parametrize(
    "config",
    [
        {"command": "gitlab-mcp"},
        {"command": []},
        {"command": ["gitlab-mcp"], "env": {"TOKEN": 1}},
        {"command": ["gitlab-mcp"], "tools": "list_projects"},
    ],
)
def test_invalid_configuration_is_refused(tmp_path: Path, config: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="MCP server 'gitlab'"):
        load_mcp_servers(write(tmp_path, {"gitlab": config}))


def test_invalid_json_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "mcp_servers.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_mcp_servers(str(path))


def test_project_file_is_valid() -> None:
    project_file = Path(__file__).resolve().parents[1] / "config" / "mcp_servers.json"
    assert load_mcp_servers(str(project_file)) == {}


def test_server_gets_only_basic_variables_and_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("GITLAB_TOKEN", "secret")
    env = server_environment({"GITHUB_PERSONAL_ACCESS_TOKEN": "x"})
    assert env["PATH"] == "/usr/bin"
    assert env["GITHUB_PERSONAL_ACCESS_TOKEN"] == "x"
    assert "GITLAB_TOKEN" not in env


TWO_TOOLS_SERVER = """
import sys
sys.path.insert(0, {src!r})
from mcp_runtime.server import MCPServer
from tools.tool import Tool, object_schema

def noop():
    return "ok"

MCPServer("test", "1", [
    Tool("read_issue", "Read.", object_schema({{}}), noop, frozenset({{"context"}})),
    Tool("create_issue", "Write.", object_schema({{}}), noop, frozenset({{"context"}}), read_only=False),
]).serve()
"""


def test_only_read_only_mcp_tools_reach_the_agents(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = str(Path(__file__).resolve().parents[1] / "src")
    code = TWO_TOOLS_SERVER.format(src=src)
    toolbox = build_toolbox([], write(tmp_path, {"issues": {"command": [sys.executable, "-c", code]}}))
    try:
        assert [tool.name for tool in toolbox.tools] == ["read_issue"]
    finally:
        toolbox.close()
    assert "not read-only, ignored: create_issue" in capsys.readouterr().err
