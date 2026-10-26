"""Discovery and loading of connectors.

Every folder connectors/<name>/ holding a connector.py module with a concrete
Connector subclass is a connector; CONNECTORS in .env says which ones are enabled.
"""

from __future__ import annotations

import importlib
import inspect
import os
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType

from connectors.base.base import Connector

# Comma-separated in .env
CONNECTORS = tuple(name.strip() for name in os.getenv("CONNECTORS", "gitlab").split(",") if name.strip())

# Folders of the connectors package that are not connectors
IGNORED_FOLDERS = frozenset({"template"})


def connector_classes_in(module: ModuleType) -> list[type[Connector]]:
    """Return the concrete Connector subclasses with a NAME defined in the module itself."""
    found: list[type[Connector]] = []
    for _, member in inspect.getmembers(module, inspect.isclass):
        if (
            issubclass(member, Connector)
            and not inspect.isabstract(member)
            and member.__module__ == module.__name__
            and member.NAME
        ):
            found.append(member)
    return found


def discover_connector_classes() -> dict[str, type[Connector]]:
    """Return the connector classes by NAME; raise ValueError if two share a NAME."""
    package = importlib.import_module("connectors")
    found: dict[str, type[Connector]] = {}
    folders = sorted(
        child
        for root in package.__path__
        for child in Path(root).iterdir()
        if child.is_dir() and child.name not in IGNORED_FOLDERS and (child / "connector.py").is_file()
    )
    for folder in folders:
        module = importlib.import_module(f"connectors.{folder.name}.connector")
        for connector_class in connector_classes_in(module):
            if connector_class.NAME in found and found[connector_class.NAME] is not connector_class:
                raise ValueError(f"Two connectors are named '{connector_class.NAME}'.")
            found[connector_class.NAME] = connector_class
    return found


def load_connectors(names: Iterable[str] = CONNECTORS) -> tuple[list[Connector], list[str]]:
    """Create the requested connectors configured in .env, with warnings for the unknown or unconfigured ones."""
    available = discover_connector_classes()
    connectors: list[Connector] = []
    warnings: list[str] = []
    for name in names:
        connector_class = available.get(name)
        if connector_class is None:
            warnings.append(f"Unknown connector: {name} (available: {', '.join(sorted(available))}).")
            continue
        connector = connector_class.from_environment()
        if connector is None:
            warnings.append(f"Connector '{name}' skipped: its configuration is missing in .env.")
            continue
        connectors.append(connector)
    return connectors, warnings
