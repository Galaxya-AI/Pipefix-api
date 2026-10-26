"""Common base of all connectors (GitLab, Prometheus, Grafana, web search...).

To add a connector: create connectors/<name>/connector.py with a Connector subclass
and add <name> to CONNECTORS in .env (see connectors/template/connector_template.py).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from tools.tool import Tool


class Connector(ABC):
    """Each subclass sets NAME (the value used in CONNECTORS) and DESCRIPTION."""

    NAME: ClassVar[str] = ""
    DESCRIPTION: ClassVar[str] = ""

    @classmethod
    @abstractmethod
    def from_environment(cls) -> Connector | None:
        """Create the connector from its .env settings; None if its configuration is missing."""

    @abstractmethod
    def tools(self) -> list[Tool]: ...
