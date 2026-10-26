"""Template of a new connector (copy it, do not enable it as is).

Steps to add a tool such as SonarQube:
1. copy this file to connectors/sonarqube/connector.py;
2. rename the class, set NAME = "sonarqube" and DESCRIPTION;
3. read the configuration at the top of the module with os.getenv;
4. write one method per tool and declare it in tools() with its tags;
5. add "sonarqube" to CONNECTORS in .env and the variables to .env.example.
This folder is ignored by the loader (see IGNORED_FOLDERS).
"""

from __future__ import annotations

import os
from typing import Any, ClassVar

from connectors.base.base import Connector
from connectors.http_client.http_api import HttpApi
from tools.tags import TAG_CONTEXT
from tools.tool import Tool, object_schema

EXAMPLE_URL = os.getenv("EXAMPLE_URL", "")
EXAMPLE_TOKEN = os.getenv("EXAMPLE_TOKEN", "")


class ExampleConnector(Connector):
    """Example connector with a single read-only tool."""

    NAME: ClassVar[str] = "example"
    DESCRIPTION: ClassVar[str] = "Example connector (template)."

    def __init__(self, api: HttpApi) -> None:
        self.api = api

    @classmethod
    def from_environment(cls) -> ExampleConnector | None:
        if not EXAMPLE_URL:
            return None
        return cls(HttpApi(EXAMPLE_URL, {"Authorization": f"Bearer {EXAMPLE_TOKEN}"}))

    def get_status(self, item: str) -> dict[str, Any]:
        status: dict[str, Any] = self.api.get_json(f"/api/items/{item}/status")
        return status

    def tools(self) -> list[Tool]:
        return [
            Tool(
                "get_item_status",
                "Return the status of an item in the external tool.",
                object_schema({"item": {"type": "string", "description": "Item identifier."}}),
                self.get_status,
                frozenset({TAG_CONTEXT}),
            )
        ]
