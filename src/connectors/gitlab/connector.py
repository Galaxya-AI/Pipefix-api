"""GitLab connector: read-only tools on pipelines and code for the agents.

The tools declare "project" and "ref" parameters that the workflow fixes for the run
(tools.tool.bind_arguments), so the LLM never sees them; over MCP they stay visible
to the external client. Commits and merge requests are made by the workflow, out of
the LLM's reach.
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import PurePosixPath
from typing import Any, ClassVar

from connectors.base.base import Connector
from connectors.gitlab.client import GitLabClient
from text_tools.log_cleaning import clean_log
from tools.errors import ToolError
from tools.tags import TAG_CI, TAG_CODE, TAG_CONTEXT
from tools.tool import Tool, object_schema

GITLAB_URL = os.getenv("GITLAB_URL", "https://gitlab.com")
# read_api scope to analyse, api scope to publish fixes
GITLAB_TOKEN = os.getenv("GITLAB_TOKEN", "")

# File extensions searched as text
TEXT_EXTENSIONS = frozenset(
    ".py .js .ts .tsx .jsx .java .kt .go .rb .php .cs .c .h .cpp .hpp .rs .scala .sh .bash .sql .yml .yaml .json "
    ".toml .ini .cfg .conf .xml .md .txt .html .css .scss .gradle .properties .tf .lock".split()
)
# Text files without a known extension
TEXT_FILE_NAMES = frozenset({"Dockerfile", "Makefile", "Jenkinsfile", ".gitlab-ci.yml"})

MAX_SEARCH_FILES = 300
MAX_SEARCH_RESULTS = 60
MAX_FILE_LINES = 400
MAX_RAW_CHARS = 1_000_000

PROJECT_PARAMETER = {"type": "string", "description": "GitLab project. Provided automatically."}
REF_PARAMETER = {"type": "string", "description": "Branch, tag or commit. Provided automatically."}


def is_text_file(path: str) -> bool:
    """Tell whether a file is probably text, from its name or extension."""
    pure = PurePosixPath(path)
    return pure.name in TEXT_FILE_NAMES or pure.suffix.lower() in TEXT_EXTENSIONS


def number_lines(text: str, start: int, end: int) -> str:
    """Return lines start to end (1-based, inclusive), prefixed with their number."""
    lines = text.splitlines()
    return "\n".join(f"{number:>5} | {lines[number - 1]}" for number in range(start, end + 1))


class GitLabConnector(Connector):
    """Read-only GitLab tools: pipelines, logs, tree, code, commits."""

    NAME: ClassVar[str] = "gitlab"
    DESCRIPTION: ClassVar[str] = "GitLab CI/CD pipelines, job logs and source code (read-only)."

    def __init__(self, client: GitLabClient) -> None:
        self.client = client
        self.tree_cache: dict[tuple[str, str], list[str]] = {}
        self.file_cache: dict[tuple[str, str, str], str] = {}
        # The API serves requests from several threads.
        self.lock = threading.Lock()

    @classmethod
    def from_environment(cls) -> GitLabConnector | None:
        if not GITLAB_TOKEN:
            return None
        return cls(GitLabClient(GITLAB_URL, GITLAB_TOKEN))

    def file_paths(self, project: str, ref: str) -> list[str]:
        """Return the paths of every file of the repository (cached)."""
        key = (project, ref)
        with self.lock:
            cached = self.tree_cache.get(key)
        if cached is not None:
            return cached
        paths = [item["path"] for item in self.client.list_tree(project, ref) if item.get("type") == "blob"]
        with self.lock:
            self.tree_cache[key] = paths
        return paths

    def raw_content(self, project: str, ref: str, path: str) -> str:
        """Return the raw content of a file (cached)."""
        key = (project, ref, path)
        with self.lock:
            cached = self.file_cache.get(key)
        if cached is not None:
            return cached
        content = self.client.file_raw(project, path, ref)
        with self.lock:
            self.file_cache[key] = content
        return content

    def list_failed_jobs(self, project: str, pipeline_id: int) -> list[dict[str, Any]]:
        jobs = self.client.pipeline_jobs(project, int(pipeline_id), scope="failed")
        return [
            {
                "id": job.get("id"),
                "name": job.get("name"),
                "stage": job.get("stage"),
                "failure_reason": job.get("failure_reason"),
                "allow_failure": job.get("allow_failure"),
                "url": job.get("web_url"),
            }
            for job in jobs
        ]

    def get_job_log(self, project: str, job_id: int, max_lines: int = 300) -> str:
        """Return the cleaned end of a job log with secrets masked; max_lines is clamped to 20..1500."""
        lines = max(20, min(int(max_lines), 1500))
        return clean_log(self.client.job_trace(project, int(job_id)), lines)

    def list_files(self, project: str, ref: str, path: str = "") -> str:
        """Return the files under a folder (empty means the root), one per line, 500 at most."""
        prefix = path.strip("/")
        matching = [p for p in self.file_paths(project, ref) if not prefix or p.startswith(prefix + "/")]
        if not matching:
            return f"No file under '{prefix or '/'}'."
        suffix = f"\n... ({len(matching) - 500} more files)" if len(matching) > 500 else ""
        return "\n".join(matching[:500]) + suffix

    def read_file(
        self, project: str, ref: str, path: str, start_line: int = 1, end_line: int = 0, raw: bool = False
    ) -> str:
        """Return a file with numbered lines (end_line 0 means automatic), or raw to copy it exactly.

        Raise ToolError if the file is too large for a raw read, or start_line is after the end.
        """
        content = self.raw_content(project, ref, path)
        if raw:
            if len(content) > MAX_RAW_CHARS:
                raise ToolError(f"File too large for a raw read ({len(content)} characters).")
            return content
        total = len(content.splitlines())
        if total == 0:
            return f"{path} is empty."
        start = max(1, int(start_line))
        if start > total:
            raise ToolError(f"{path} only has {total} lines.")
        end = min(int(end_line) if int(end_line) > 0 else total, total, start + MAX_FILE_LINES - 1)
        return f"{path} - lines {start} to {end} of {total}\n{number_lines(content, start, end)}"

    def search_code(self, project: str, ref: str, pattern: str, path_prefix: str = "") -> str:
        """Search a regular expression (or literal text if it is not a valid one) in the text files.

        Return one "path:line: text" per match.
        """
        try:
            regex = re.compile(pattern)
        except re.error:
            regex = re.compile(re.escape(pattern))
        prefix = path_prefix.strip("/")
        candidates = [
            p
            for p in self.file_paths(project, ref)
            if is_text_file(p) and (not prefix or p == prefix or p.startswith(prefix + "/"))
        ][:MAX_SEARCH_FILES]
        results: list[str] = []
        for candidate in candidates:
            try:
                text = self.raw_content(project, ref, candidate)
            # An unreadable file must not stop the search.
            except Exception:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    results.append(f"{candidate}:{number}: {line.strip()[:200]}")
                    if len(results) >= MAX_SEARCH_RESULTS:
                        return "\n".join(results) + "\n... (results limited, refine the search)"
        return "\n".join(results) if results else f"No result for '{pattern}'."

    def get_recent_commits(self, project: str, ref: str, limit: int = 10) -> list[dict[str, Any]]:
        """Return the latest commits up to the analysed reference; limit is clamped to 1..30."""
        commits = self.client.recent_commits(project, ref, max(1, min(int(limit), 30)))
        return [
            {
                "sha": commit.get("short_id"),
                "title": commit.get("title"),
                "author": commit.get("author_name"),
                "date": commit.get("created_at"),
            }
            for commit in commits
        ]

    def get_commit_changes(self, project: str, sha: str) -> str:
        parts = [
            f"--- {item.get('old_path')}\n+++ {item.get('new_path')}\n{item.get('diff', '')}"
            for item in self.client.commit_diff(project, sha)
        ]
        return "\n".join(parts) or "No changes."

    def tools(self) -> list[Tool]:
        return [
            Tool(
                "list_failed_jobs",
                "List the failed jobs of a pipeline with their stage and the reason given by GitLab.",
                object_schema({"project": PROJECT_PARAMETER, "pipeline_id": {"type": "integer"}}),
                self.list_failed_jobs,
                frozenset({TAG_CI}),
            ),
            Tool(
                "get_job_log",
                "Return the end of a job log (cleaned, secrets masked). Start with 300 lines; more if needed.",
                object_schema(
                    {
                        "project": PROJECT_PARAMETER,
                        "job_id": {"type": "integer", "description": "Job id."},
                        "max_lines": {"type": "integer", "description": "Final lines to read (20 to 1500)."},
                    },
                    required=["project", "job_id"],
                ),
                self.get_job_log,
                frozenset({TAG_CI}),
            ),
            Tool(
                "list_files",
                "List the repository files, optionally under a folder.",
                object_schema(
                    {"project": PROJECT_PARAMETER, "ref": REF_PARAMETER, "path": {"type": "string"}},
                    required=["project", "ref"],
                ),
                self.list_files,
                frozenset({TAG_CODE}),
            ),
            Tool(
                "read_file",
                "Read a file. Default: numbered lines (400 max). raw=true: exact content, needed before propose_edit.",
                object_schema(
                    {
                        "project": PROJECT_PARAMETER,
                        "ref": REF_PARAMETER,
                        "path": {"type": "string", "description": "File path."},
                        "start_line": {"type": "integer", "description": "First line (default 1)."},
                        "end_line": {"type": "integer", "description": "Last line (0 = automatic)."},
                        "raw": {"type": "boolean", "description": "true = exact content, without line numbers."},
                    },
                    required=["project", "ref", "path"],
                ),
                self.read_file,
                frozenset({TAG_CODE}),
            ),
            Tool(
                "search_code",
                "Search a text or a regular expression in the text files of the repository.",
                object_schema(
                    {
                        "project": PROJECT_PARAMETER,
                        "ref": REF_PARAMETER,
                        "pattern": {"type": "string", "description": "Text or regular expression."},
                        "path_prefix": {"type": "string", "description": "Folder to search in (empty = everywhere)."},
                    },
                    required=["project", "ref", "pattern"],
                ),
                self.search_code,
                frozenset({TAG_CODE}),
            ),
            Tool(
                "get_recent_commits",
                "List the latest commits up to the analysed version, to spot the faulty change.",
                object_schema(
                    {"project": PROJECT_PARAMETER, "ref": REF_PARAMETER, "limit": {"type": "integer"}},
                    required=["project", "ref"],
                ),
                self.get_recent_commits,
                frozenset({TAG_CI, TAG_CODE, TAG_CONTEXT}),
            ),
            Tool(
                "get_commit_changes",
                "Show the changes made by a commit.",
                object_schema({"project": PROJECT_PARAMETER, "sha": {"type": "string", "description": "Commit id."}}),
                self.get_commit_changes,
                frozenset({TAG_CODE}),
            ),
        ]
