"""Objects used by the API routes: launcher, store, tools and secrets."""

from __future__ import annotations

from dataclasses import dataclass, field

from app_runs.launcher import Launcher
from app_runs.run_store import RunStore
from tools.tool import Tool


@dataclass
class ApiServices:
    launcher: Launcher
    store: RunStore
    # Listed by GET /api/v1/tools.
    tools: list[Tool] = field(default_factory=list)
    # Bearer token required on /api/v1 routes; empty disables the check.
    api_token: str = ""
    # Expected in the X-Gitlab-Token header; empty disables the webhook.
    webhook_secret: str = ""
    # Dry-run mode of the runs started by the webhook.
    default_dry_run: bool = True
