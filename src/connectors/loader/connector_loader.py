"""Discovery and loading of connectors.

Convention: every folder connectors/<name>/ holding a connector.py module
with a concrete Connector subclass is a connector. The CONNECTORS variable
of .env says which ones are enabled.
"""

from __future__ import annotations

import importlib
import inspect
import os
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType
from typing import Any

from connectors.base.base import Connector

# Enabled connectors, comma-separated
CONNECTORS: tuple[str, ...] = tuple(
    name.strip() for name in os.getenv("CONNECTORS", "gitlab").split(",") if name.strip()
)

# Folders of the connectors package that are not connectors
IGNORED_FOLDERS: frozenset[str] = frozenset({"template"})


def connector_classes_in(module: ModuleType) -> list[type[Connector]]:
    """Return the concrete connector classes defined in a module.

    Parameters
    ----------
    module : ModuleType
        Imported connector module.

    Returns
    -------
    list[type[Connector]]
        Concrete subclasses of Connector with a NAME.
    """
    # Classes found
    found: list[type[Connector]] = []
    # Current class
    member: Any
    # Every class of the module
    for _, member in inspect.getmembers(module, inspect.isclass):
        # Concrete connector defined in this module, with a name
        if (
            issubclass(member, Connector)
            and not inspect.isabstract(member)
            and member.__module__ == module.__name__
            and member.NAME
        ):
            # Keep it
            found.append(member)
    # Classes found
    return found


def discover_connector_classes() -> dict[str, type[Connector]]:
    """Find every connector class of the connectors package.

    Returns
    -------
    dict[str, type[Connector]]
        Connector classes by NAME.

    Raises
    ------
    ValueError
        If two connectors have the same NAME.
    """
    # The connectors package
    package: ModuleType = importlib.import_module("connectors")
    # Classes by name
    found: dict[str, type[Connector]] = {}
    # Connector folders: sub-folders holding a connector.py file
    folders: list[Path] = sorted(
        child
        for root in package.__path__
        for child in Path(root).iterdir()
        if child.is_dir() and child.name not in IGNORED_FOLDERS and (child / "connector.py").is_file()
    )
    # Current folder
    folder: Path
    # Every connector folder
    for folder in folders:
        # Import connectors/<name>/connector.py
        module: ModuleType = importlib.import_module(f"connectors.{folder.name}.connector")
        # Current class
        connector_class: type[Connector]
        # Register every class of the module
        for connector_class in connector_classes_in(module):
            # Same name already used by another class
            if connector_class.NAME in found and found[connector_class.NAME] is not connector_class:
                # Configuration error
                raise ValueError(f"Two connectors are named '{connector_class.NAME}'.")
            # Registration
            found[connector_class.NAME] = connector_class
    # Classes by name
    return found


def load_connectors(names: Iterable[str] = CONNECTORS) -> tuple[list[Connector], list[str]]:
    """Create the requested connectors that are configured in .env.

    Parameters
    ----------
    names : Iterable[str], default CONNECTORS
        Names of the connectors to enable.

    Returns
    -------
    tuple[list[Connector], list[str]]
        The ready connectors, and warnings for the unknown or unconfigured ones.
    """
    # Every existing connector
    available: dict[str, type[Connector]] = discover_connector_classes()
    # Ready connectors
    connectors: list[Connector] = []
    # Warnings for the user
    warnings: list[str] = []
    # Current name
    name: str
    # Every requested connector
    for name in names:
        # Class of the connector
        connector_class: type[Connector] | None = available.get(name)
        # Unknown connector
        if connector_class is None:
            # Warning with the valid names
            warnings.append(f"Unknown connector: {name} (available: {', '.join(sorted(available))}).")
            # Next one
            continue
        # Creation from .env
        connector: Connector | None = connector_class.from_environment()
        # Missing configuration
        if connector is None:
            # Warning
            warnings.append(f"Connector '{name}' skipped: its configuration is missing in .env.")
            # Next one
            continue
        # Ready connector
        connectors.append(connector)
    # Connectors and warnings
    return connectors, warnings
