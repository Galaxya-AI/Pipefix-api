"""Data policy: keeps confidential code away from external LLMs and web search.

Three settings in .env:
- CONFIDENTIAL_PROJECTS: projects whose data may only go to internal LLM
  providers (INTERNAL_LLM_PROVIDERS, e.g. "ollama"). If an agent of the run
  uses another provider, the run stops before reading anything. Web search
  tools are also removed for these projects.
- DENIED_PATHS: glob patterns of files the agents may never read or change
  (e.g. "src/pricing/**", "**/*.pem"). Every tool is wrapped: a denied path in
  the arguments is refused, and denied paths are removed from listings.
Every refusal is written to the run journal.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any

from journal.run_journal import RunJournal
from tools.errors import ToolError
from tools.tool import Tool

# "group/project" paths or numeric ids
CONFIDENTIAL_PROJECTS = tuple(
    item.strip() for item in os.getenv("CONFIDENTIAL_PROJECTS", "").split(",") if item.strip()
)

DENIED_PATHS = tuple(item.strip() for item in os.getenv("DENIED_PATHS", "").split(",") if item.strip())

# Providers that run inside the company, allowed for confidential projects
INTERNAL_LLM_PROVIDERS = tuple(
    item.strip() for item in os.getenv("INTERNAL_LLM_PROVIDERS", "").split(",") if item.strip()
)

PATH_ARGUMENTS = frozenset({"path", "file_path", "path_prefix"})

# Tools whose text result lists file paths, one per line ("path" or "path:line: text")
LISTING_TOOLS = frozenset({"list_files", "search_code"})


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a path glob into a regular expression anchored on the whole path.

    "**" matches any folders (even none), "*" and "?" never cross a "/".
    A pattern ending with "/**" also matches the folder itself.
    """
    text = pattern.strip().strip("/")
    parts: list[str] = []
    index = 0
    while index < len(text):
        if text.startswith("**/", index):
            parts.append("(?:.*/)?")
            index += 3
        elif text.startswith("/**", index) and index + 3 == len(text):
            parts.append("(?:/.*)?")
            index += 3
        elif text.startswith("**", index):
            parts.append(".*")
            index += 2
        elif text[index] == "*":
            parts.append("[^/]*")
            index += 1
        elif text[index] == "?":
            parts.append("[^/]")
            index += 1
        else:
            parts.append(re.escape(text[index]))
            index += 1
    return re.compile("^" + "".join(parts) + "$")


def normalize(identifier: Any) -> str:
    """Return a project path or id in a comparable form."""
    return str(identifier).strip().strip("/").lower()


@dataclass(frozen=True)
class DataPolicy:
    # Normalised project identifiers
    confidential_projects: frozenset[str] = frozenset()
    denied_patterns: tuple[str, ...] = ()
    internal_providers: frozenset[str] = frozenset()

    @classmethod
    def from_environment(
        cls,
        confidential_projects: Iterable[str] = CONFIDENTIAL_PROJECTS,
        denied_paths: Iterable[str] = DENIED_PATHS,
        internal_providers: Iterable[str] = INTERNAL_LLM_PROVIDERS,
    ) -> DataPolicy:
        return cls(
            frozenset(normalize(item) for item in confidential_projects if str(item).strip()),
            tuple(item.strip() for item in denied_paths if item.strip()),
            frozenset(item.strip() for item in internal_providers if item.strip()),
        )

    def denied_regexes(self) -> list[re.Pattern[str]]:
        return [glob_to_regex(pattern) for pattern in self.denied_patterns]

    def is_confidential(self, identifiers: Iterable[Any]) -> bool:
        """True if any known identifier of the project (path, numeric id...) is listed."""
        names = {normalize(item) for item in identifiers if item is not None and str(item).strip()}
        return bool(names & self.confidential_projects)

    def is_denied(self, path: str) -> bool:
        clean = str(path).strip().strip("/")
        return bool(clean) and any(regex.match(clean) for regex in self.denied_regexes())

    def external_providers(self, providers: Iterable[str]) -> list[str]:
        """Return the sorted providers that are not internal."""
        return sorted({provider for provider in providers if provider not in self.internal_providers})

    def as_dict(self) -> dict[str, Any]:
        return {
            "confidential_projects": sorted(self.confidential_projects),
            "denied_paths": list(self.denied_patterns),
            "internal_llm_providers": sorted(self.internal_providers),
        }

    def filter_listing(self, text: str) -> str:
        """Remove denied files from a listing ("path" or "path:line: text" per line)."""
        return "\n".join(line for line in text.splitlines() if not self.is_denied(line.split(":", 1)[0]))

    def guard(self, tool: Tool, journal: RunJournal | None = None) -> Tool:
        """Wrap a tool so that it never reads, lists or changes a denied path.

        Returns the tool itself when no pattern is set.
        """
        if not self.denied_patterns:
            return tool
        original = tool.function

        def guarded(**arguments: Any) -> Any:
            for name, value in arguments.items():
                if name in PATH_ARGUMENTS and isinstance(value, str) and self.is_denied(value):
                    if journal is not None:
                        journal.log("policy", "access_denied", {"tool": tool.name, "path": value})
                    # Sent back to the LLM as a tool error, not raised to the workflow
                    raise ToolError(f"Access denied by the data policy: {value}")
            result = original(**arguments)
            if tool.name in LISTING_TOOLS and isinstance(result, str):
                return self.filter_listing(result)
            return result

        return replace(tool, function=guarded)
