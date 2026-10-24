"""GitLab connector: read-only tools on pipelines and code for the agents.

The tools declare "project" and "ref" parameters; the workflow fixes them
for the run (tools.tool.bind_arguments), so the LLM never sees them. Over MCP,
these parameters stay visible to the external client.
All tools are read-only: commits and merge requests are made by the
workflow, out of the LLM's reach.
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

# Address of the GitLab instance
GITLAB_URL: str = os.getenv("GITLAB_URL", "https://gitlab.com")

# GitLab access token (read_api to analyse, api to publish fixes)
GITLAB_TOKEN: str = os.getenv("GITLAB_TOKEN", "")

# File extensions searched as text
TEXT_EXTENSIONS: frozenset[str] = frozenset(
    ".py .js .ts .tsx .jsx .java .kt .go .rb .php .cs .c .h .cpp .hpp .rs .scala .sh .bash .sql .yml .yaml .json "
    ".toml .ini .cfg .conf .xml .md .txt .html .css .scss .gradle .properties .tf .lock".split()
)

# Text files without a known extension
TEXT_FILE_NAMES: frozenset[str] = frozenset({"Dockerfile", "Makefile", "Jenkinsfile", ".gitlab-ci.yml"})

# Maximum number of files scanned by one search
MAX_SEARCH_FILES: int = 300

# Maximum number of matching lines returned by one search
MAX_SEARCH_RESULTS: int = 60

# Maximum number of lines returned by one numbered read
MAX_FILE_LINES: int = 400

# Maximum size of a raw read, in characters
MAX_RAW_CHARS: int = 1_000_000

# Schema of the "project" parameter
PROJECT_PARAMETER: dict[str, str] = {"type": "string", "description": "GitLab project. Provided automatically."}

# Schema of the "ref" parameter
REF_PARAMETER: dict[str, str] = {"type": "string", "description": "Branch, tag or commit. Provided automatically."}


def is_text_file(path: str) -> bool:
    """Tell whether a file is probably text (code, configuration, documentation).

    Parameters
    ----------
    path : str
        File path.

    Returns
    -------
    bool
        True for a known text name or extension.
    """
    # Parsed path
    pure: PurePosixPath = PurePosixPath(path)
    # Known name or text extension
    return pure.name in TEXT_FILE_NAMES or pure.suffix.lower() in TEXT_EXTENSIONS


def number_lines(text: str, start: int, end: int) -> str:
    """Return lines start to end of a text, prefixed with their number.

    Parameters
    ----------
    text : str
        Full text.
    start : int
        First line, from 1.
    end : int
        Last line, included.

    Returns
    -------
    str
        Numbered lines.
    """
    # Lines of the text
    lines: list[str] = text.splitlines()
    # Number and content of each requested line
    return "\n".join(f"{number:>5} | {lines[number - 1]}" for number in range(start, end + 1))


class GitLabConnector(Connector):
    """Read-only GitLab tools: pipelines, logs, tree, code, commits.

    Parameters
    ----------
    client : GitLabClient
        GitLab API client.
    """

    # Name used in CONNECTORS
    NAME: ClassVar[str] = "gitlab"
    # Description
    DESCRIPTION: ClassVar[str] = "GitLab CI/CD pipelines, job logs and source code (read-only)."

    def __init__(self, client: GitLabClient) -> None:
        # GitLab API client
        self.client: GitLabClient = client
        # File paths by (project, ref)
        self.tree_cache: dict[tuple[str, str], list[str]] = {}
        # File contents by (project, ref, path)
        self.file_cache: dict[tuple[str, str, str], str] = {}
        # Protects the caches (the API runs several threads)
        self.lock: threading.Lock = threading.Lock()

    @classmethod
    def from_environment(cls) -> GitLabConnector | None:
        """Create the connector from GITLAB_URL and GITLAB_TOKEN.

        Returns
        -------
        GitLabConnector or None
            The connector, or None if GITLAB_TOKEN is missing.
        """
        # No token: not configured
        if not GITLAB_TOKEN:
            # Connector skipped
            return None
        # Ready connector
        return cls(GitLabClient(GITLAB_URL, GITLAB_TOKEN))

    def file_paths(self, project: str, ref: str) -> list[str]:
        """Return the paths of every file of the repository, cached.

        Parameters
        ----------
        project : str
            Project.
        ref : str
            Branch, tag or commit.

        Returns
        -------
        list[str]
            File paths.
        """
        # Cache key
        key: tuple[str, str] = (project, ref)
        # Protected read
        with self.lock:
            # Cached paths, if any
            cached: list[str] | None = self.tree_cache.get(key)
        # Already known
        if cached is not None:
            # Returned at once
            return cached
        # Files only, not folders
        paths: list[str] = [item["path"] for item in self.client.list_tree(project, ref) if item.get("type") == "blob"]
        # Protected write
        with self.lock:
            # Store in the cache
            self.tree_cache[key] = paths
        # Paths
        return paths

    def raw_content(self, project: str, ref: str, path: str) -> str:
        """Return the raw content of a file, cached.

        Parameters
        ----------
        project : str
            Project.
        ref : str
            Branch, tag or commit.
        path : str
            File path.

        Returns
        -------
        str
            File content.
        """
        # Cache key
        key: tuple[str, str, str] = (project, ref, path)
        # Protected read
        with self.lock:
            # Cached content, if any
            cached: str | None = self.file_cache.get(key)
        # Already read
        if cached is not None:
            # Returned at once
            return cached
        # Read from GitLab
        content: str = self.client.file_raw(project, path, ref)
        # Protected write
        with self.lock:
            # Store in the cache
            self.file_cache[key] = content
        # Content
        return content

    def list_failed_jobs(self, project: str, pipeline_id: int) -> list[dict[str, Any]]:
        """Return the failed jobs of a pipeline.

        Parameters
        ----------
        project : str
            Project.
        pipeline_id : int
            Pipeline id.

        Returns
        -------
        list[dict[str, Any]]
            Summary of each failed job.
        """
        # Failed jobs
        jobs: list[dict[str, Any]] = self.client.pipeline_jobs(project, int(pipeline_id), scope="failed")
        # Useful fields only
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
        """Return the end of a job log, cleaned and with secrets masked.

        Parameters
        ----------
        project : str
            Project.
        job_id : int
            Job id.
        max_lines : int, default 300
            Final lines to keep (20 to 1500).

        Returns
        -------
        str
            Cleaned end of the log.
        """
        # Number of lines kept within its limits
        lines: int = max(20, min(int(max_lines), 1500))
        # Read then clean
        return clean_log(self.client.job_trace(project, int(job_id)), lines)

    def list_files(self, project: str, ref: str, path: str = "") -> str:
        """Return the repository files under a folder.

        Parameters
        ----------
        project : str
            Project.
        ref : str
            Branch, tag or commit.
        path : str, default ""
            Folder; empty means the root.

        Returns
        -------
        str
            One path per line (500 at most).
        """
        # Folder without surrounding "/"
        prefix: str = path.strip("/")
        # Files under the folder
        matching: list[str] = [p for p in self.file_paths(project, ref) if not prefix or p.startswith(prefix + "/")]
        # No file
        if not matching:
            # Clear message
            return f"No file under '{prefix or '/'}'."
        # Note when the list is cut
        suffix: str = f"\n... ({len(matching) - 500} more files)" if len(matching) > 500 else ""
        # Paths
        return "\n".join(matching[:500]) + suffix

    def read_file(
        self, project: str, ref: str, path: str, start_line: int = 1, end_line: int = 0, raw: bool = False
    ) -> str:
        """Return a file, with numbered lines (to read) or raw (to copy exactly).

        Parameters
        ----------
        project : str
            Project.
        ref : str
            Branch, tag or commit.
        path : str
            File path.
        start_line : int, default 1
            First line of a numbered read.
        end_line : int, default 0
            Last line of a numbered read; 0 means automatic.
        raw : bool, default False
            True returns the exact content, without numbers.

        Returns
        -------
        str
            Content of the file.

        Raises
        ------
        ToolError
            If the file is too large for a raw read, or start_line is after the end.
        """
        # Content of the file
        content: str = self.raw_content(project, ref, path)
        # Exact copy requested
        if raw:
            # Huge file
            if len(content) > MAX_RAW_CHARS:
                # Refused
                raise ToolError(f"File too large for a raw read ({len(content)} characters).")
            # Exact content
            return content
        # Number of lines
        total: int = len(content.splitlines())
        # Empty file
        if total == 0:
            # Clear message
            return f"{path} is empty."
        # First line, at least 1
        start: int = max(1, int(start_line))
        # Start after the end
        if start > total:
            # Clear error
            raise ToolError(f"{path} only has {total} lines.")
        # Last line: requested or automatic, within the limits
        end: int = min(int(end_line) if int(end_line) > 0 else total, total, start + MAX_FILE_LINES - 1)
        # Header and numbered lines
        return f"{path} - lines {start} to {end} of {total}\n{number_lines(content, start, end)}"

    def search_code(self, project: str, ref: str, pattern: str, path_prefix: str = "") -> str:
        """Search a text or a regular expression in the text files of the repository.

        Parameters
        ----------
        project : str
            Project.
        ref : str
            Branch, tag or commit.
        pattern : str
            Text or regular expression.
        path_prefix : str, default ""
            Folder to search in; empty means everywhere.

        Returns
        -------
        str
            "path:line: text" for each match.
        """
        # Compile the pattern as a regular expression
        try:
            # Regular expression
            regex: re.Pattern[str] = re.compile(pattern)
        # Invalid expression
        except re.error:
            # Literal search
            regex = re.compile(re.escape(pattern))
        # Folder without surrounding "/"
        prefix: str = path_prefix.strip("/")
        # Text files under the folder, capped
        candidates: list[str] = [
            p
            for p in self.file_paths(project, ref)
            if is_text_file(p) and (not prefix or p == prefix or p.startswith(prefix + "/"))
        ][:MAX_SEARCH_FILES]
        # Matching lines
        results: list[str] = []
        # Current file
        candidate: str
        # Scan every file
        for candidate in candidates:
            # Read the file
            try:
                # Its content
                text: str = self.raw_content(project, ref, candidate)
            # An unreadable file must not stop the search
            except Exception:
                # Next file
                continue
            # Current line number
            number: int
            # Current line
            line: str
            # Scan every line
            for number, line in enumerate(text.splitlines(), start=1):
                # Match
                if regex.search(line):
                    # Keep it
                    results.append(f"{candidate}:{number}: {line.strip()[:200]}")
                    # Cap reached
                    if len(results) >= MAX_SEARCH_RESULTS:
                        # Stop with a note
                        return "\n".join(results) + "\n... (results limited, refine the search)"
        # Matches or a clear message
        return "\n".join(results) if results else f"No result for '{pattern}'."

    def get_recent_commits(self, project: str, ref: str, limit: int = 10) -> list[dict[str, Any]]:
        """Return the latest commits up to the analysed reference.

        Parameters
        ----------
        project : str
            Project.
        ref : str
            Branch, tag or commit.
        limit : int, default 10
            Number of commits (1 to 30).

        Returns
        -------
        list[dict[str, Any]]
            Short summary of each commit.
        """
        # Commits, limit kept between 1 and 30
        commits: list[dict[str, Any]] = self.client.recent_commits(project, ref, max(1, min(int(limit), 30)))
        # Useful fields only
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
        """Return the changes made by a commit.

        Parameters
        ----------
        project : str
            Project.
        sha : str
            Commit id.

        Returns
        -------
        str
            Diff of each changed file.
        """
        # One block per changed file
        parts: list[str] = [
            f"--- {item.get('old_path')}\n+++ {item.get('new_path')}\n{item.get('diff', '')}"
            for item in self.client.commit_diff(project, sha)
        ]
        # Diff, or a clear message
        return "\n".join(parts) or "No changes."

    def tools(self) -> list[Tool]:
        """Return the read-only GitLab tools.

        Returns
        -------
        list[Tool]
            Tools tagged ci, code or context.
        """
        # Tools of the connector
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
