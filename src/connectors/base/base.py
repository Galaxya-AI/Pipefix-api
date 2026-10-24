"""Common base of all connectors (GitLab, Prometheus, Grafana, web search...).

A connector links the system to an external tool. It:
- reads its own configuration from .env (from_environment);
- exposes tools, tagged for the right agents (tools).

To add SonarQube, Nexus or Kubernetes: create connectors/<name>/connector.py
with a class inheriting from Connector, and add <name> to CONNECTORS in .env.
No other file changes (see connectors/template/connector_template.py).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from tools.tool import Tool


class Connector(ABC):
    """Base class of all connectors.

    Each subclass sets NAME (the value used in CONNECTORS) and DESCRIPTION.
    """

    # Short name used in the CONNECTORS variable
    NAME: ClassVar[str] = ""
    # One sentence describing the connector
    DESCRIPTION: ClassVar[str] = ""

    @classmethod
    @abstractmethod
    def from_environment(cls) -> Connector | None:
        """Create the connector from its module constants (read from .env).

        Returns
        -------
        Connector or None
            The connector, or None if its configuration is missing.
        """

    @abstractmethod
    def tools(self) -> list[Tool]:
        """Return the tools of the connector, with their tags.

        Returns
        -------
        list[Tool]
            Tools exposed to the agents.
        """
