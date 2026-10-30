"""Startup services: create and connect the objects of the system, for the API and the workers.

Only construction lives here: no business logic, no call to GitLab. The settings
are environment variables, read with os.getenv by each module.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from typing import Any

from agent_base.agent_base import AgentConfig
from agents.agent_factory import build_agents
from app_runs.launcher import Launcher, RunFunction
from app_runs.run_store import RunStore
from app_runs.services import ApiServices
from app_runs.thread_launcher import ThreadLauncher
from connectors.base.base import Connector
from connectors.gitlab.client import GitLabClient
from connectors.gitlab.connector import GITLAB_TOKEN, GITLAB_URL, GitLabConnector
from connectors.loader.connector_loader import load_connectors
from journal.run_journal import RunJournal
from llm.base import LLMClient
from llm.provider_factory import build_llm_client
from redis_jobs.branch_lock import RUN_TIMEOUT_S
from redis_jobs.queue_launcher import QueueLauncher
from redis_jobs.redis_connection import REDIS_URL, connect_redis
from redis_jobs.status_backend import RedisStatusBackend
from tools.tool import Tool
from workflow_analysis.toolbox import Toolbox, build_toolbox
from workflow_remediation.remediation_workflow import (
    DRY_RUN,
    MAX_ROUNDS,
    PIPELINE_TIMEOUT_S,
    WAIT_FOR_PIPELINE,
    RemediationWorkflow,
)
from workflow_remediation.round_result import RoundResult

# Empty: no check, local use only.
API_TOKEN = os.getenv("API_TOKEN", "")

# Expected in the X-Gitlab-Token header of the webhook (empty: webhook disabled).
GITLAB_WEBHOOK_SECRET = os.getenv("GITLAB_WEBHOOK_SECRET", "")


def check_run_timeout(
    wait_for_pipeline: bool = WAIT_FOR_PIPELINE,
    max_rounds: int = MAX_ROUNDS,
    pipeline_timeout_s: int = PIPELINE_TIMEOUT_S,
    run_timeout_s: int = RUN_TIMEOUT_S,
) -> None:
    """Exit if waiting for the fix pipelines alone can last longer than a queued run is allowed to."""
    if wait_for_pipeline and max_rounds * pipeline_timeout_s >= run_timeout_s:
        raise SystemExit(
            f"MAX_ROUNDS x PIPELINE_TIMEOUT_S ({max_rounds} x {pipeline_timeout_s} s) must stay below "
            f"RUN_TIMEOUT_S ({run_timeout_s} s), with room for the LLM calls: the worker would stop the run."
        )


def enabled_connectors() -> list[Connector]:
    """Load the connectors listed in CONNECTORS; warnings for unknown or unconfigured ones go to stderr."""
    connectors, warnings = load_connectors()
    for warning in warnings:
        print(f"[warning] {warning}", file=sys.stderr)
    return connectors


def gitlab_client() -> GitLabClient:
    """Return the GitLab client used by the workflow; exit if GITLAB_TOKEN is missing."""
    if not GITLAB_TOKEN:
        raise SystemExit("GITLAB_TOKEN is missing in .env.")
    return GitLabClient(GITLAB_URL, GITLAB_TOKEN)


def build_run_function(
    platform: GitLabClient, llm: LLMClient, agents: dict[str, AgentConfig], tools: list[Tool]
) -> RunFunction:
    """Return the function that runs one remediation, shared by run, watch and serve."""

    def run_remediation(project: str, pipeline_id: int | None, dry_run: bool, journal: RunJournal) -> dict[str, Any]:
        # A new workflow for each run.
        workflow = RemediationWorkflow(platform, llm, agents, tools, journal, dry_run=dry_run)
        return workflow.run(project, pipeline_id)

    return run_remediation


def user_tools(client: GitLabClient, tools: list[Tool]) -> list[Tool]:
    """The GitLab tools of the user's client, then the server's other tools."""
    gitlab_tools = GitLabConnector(client).tools()
    names = {tool.name for tool in gitlab_tools}
    return [*gitlab_tools, *(tool for tool in tools if tool.name not in names)]


def build_user_run_function(gitlab_url: str, gitlab_token: str, tools: list[Tool]) -> RunFunction:
    """Run function of a web user: GitLab is read with the user's own token, the LLMs stay the server's."""
    client = GitLabClient(gitlab_url, gitlab_token)
    return build_run_function(client, build_llm_client(), build_agents(), user_tools(client, tools))


def build_user_loop_function(
    gitlab_url: str,
    gitlab_token: str,
    tools: list[Tool],
    pipeline: dict[str, Any],
    first_result: RoundResult,
    branch: str,
) -> RunFunction:
    """Run function of the web button "fix until the pipeline passes".

    It publishes the fix the visitor accepted, waits for GitLab CI and runs new rounds while it fails,
    whatever DRY_RUN and WAIT_FOR_PIPELINE say: the visitor asked for exactly this. MAX_ROUNDS still applies.
    The token stays in this function only for the time of the run.
    """
    client = GitLabClient(gitlab_url, gitlab_token)
    llm = build_llm_client()
    agents = build_agents()
    run_tools = user_tools(client, tools)

    def run_loop(project: str, pipeline_id: int | None, dry_run: bool, journal: RunJournal) -> dict[str, Any]:
        workflow = RemediationWorkflow(
            client, llm, agents, run_tools, journal, dry_run=False, wait_for_pipeline=True
        )
        return workflow.run_saved_fix(project, pipeline, first_result, branch)

    return run_loop


def build_job_services() -> tuple[RunFunction, Callable[[], None]]:
    """Build the services of one worker job: the run function, and the function that releases its resources."""
    check_run_timeout()
    toolbox = build_toolbox(enabled_connectors())
    run_function = build_run_function(gitlab_client(), build_llm_client(), build_agents(), toolbox.tools)
    return run_function, toolbox.close


def build_api_services() -> tuple[ApiServices, Toolbox]:
    """Build the services of the REST API: Redis queue when REDIS_URL is set, threads otherwise.

    The returned toolbox must be closed when the API stops.
    """
    toolbox = build_toolbox(enabled_connectors())
    platform = gitlab_client()
    launcher: Launcher
    if REDIS_URL:
        check_run_timeout()
        # connect_redis checks the connection now, not at the first run.
        redis = connect_redis()
        store = RunStore(statuses=RedisStatusBackend(redis))
        launcher = QueueLauncher(redis, store, platform)
    else:
        store = RunStore()
        launcher = ThreadLauncher(
            build_run_function(platform, build_llm_client(), build_agents(), toolbox.tools), store
        )
    services = ApiServices(
        launcher=launcher,
        store=store,
        tools=toolbox.tools,
        api_token=API_TOKEN,
        webhook_secret=GITLAB_WEBHOOK_SECRET,
        default_dry_run=DRY_RUN,
    )
    if not API_TOKEN:
        print("[warning] API_TOKEN is empty: anyone who can reach the API can start runs.", file=sys.stderr)
    return services, toolbox
