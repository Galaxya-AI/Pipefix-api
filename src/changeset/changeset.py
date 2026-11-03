"""Changeset: the changes proposed by the fixer, kept in memory until publication.

The fixer never writes to the repository. It proposes edits ("replace this
exact text by that text") that are recorded here. The reviewer reads the
resulting diff, and the workflow publishes it only after an approved review.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable
from typing import Any

from tools.errors import ToolError
from tools.tags import TAG_EDIT, TAG_REVIEW
from tools.tool import Tool, object_schema


def unified_diff(path: str, before: str, after: str) -> str:
    lines = list(
        difflib.unified_diff(
            before.splitlines(keepends=True), after.splitlines(keepends=True), fromfile=f"a/{path}", tofile=f"b/{path}"
        )
    )
    # A last line without a newline would otherwise be glued to the next file's diff
    return "".join(line if line.endswith("\n") else line + "\n" for line in lines)


class Changeset:
    """Pending changes, file by file; `read_file` returns the exact content of a repository file."""

    def __init__(
        self,
        read_file: Callable[[str], str],
        check: Callable[[dict[str, tuple[str, str]]], list[str]] | None = None,
    ) -> None:
        """`check` returns the problems a change adds (e.g. pyflakes): propose_edits refuses such edits."""
        self.read_file = read_file
        self.check = check
        # path -> (original content, changed content)
        self.files: dict[str, tuple[str, str]] = {}

    def current(self, path: str) -> str:
        """Return the content of a file with the changes already proposed."""
        if path in self.files:
            return self.files[path][1]
        return self.read_file(path)

    def propose_edit(self, path: str, old_text: str, new_text: str) -> str:
        """Replace one exact excerpt that must appear once; return the diff of this change.

        Raises ToolError if the excerpt is empty, unchanged, not found or not unique.
        """
        if not old_text:
            raise ToolError("old_text is empty: copy the exact excerpt to replace.")
        if old_text == new_text:
            raise ToolError("old_text and new_text are identical: nothing to change.")
        content = self.current(path)
        count = content.count(old_text)
        if count == 0:
            # Detailed so the LLM can correct its call
            raise ToolError(
                f"Excerpt not found in {path}. Read the file again with read_file(raw=true) and copy the excerpt "
                "exactly, with spaces and indentation, without line numbers."
            )
        if count > 1:
            raise ToolError(f"Excerpt found {count} times in {path}: add surrounding lines to make it unique.")
        updated = content.replace(old_text, new_text, 1)
        # The original is the content before the first change, not before this one
        original = self.files[path][0] if path in self.files else content
        self.files[path] = (original, updated)
        return unified_diff(path, content, updated)

    def propose_edits(self, edits: list[dict[str, Any]]) -> str:
        """Apply several edits in one call, all or none; return the diff of every edit.

        Each edit is {"path", "old_text", "new_text"}; edits of the same file apply in order.
        Raises ToolError naming the first edit that fails; then no edit of the call is kept.
        """
        if not isinstance(edits, list) or not edits:
            raise ToolError("edits is empty: give at least one {path, old_text, new_text}.")
        saved = dict(self.files)
        diffs = []
        for index, edit in enumerate(edits, start=1):
            try:
                if not isinstance(edit, dict) or not str(edit.get("path", "")).strip():
                    raise ToolError("each edit needs a path, an old_text and a new_text.")
                path = str(edit["path"]).strip()
                diffs.append(self.propose_edit(path, str(edit.get("old_text", "")), str(edit.get("new_text", ""))))
            except ToolError as error:
                self.files = saved
                shown = edit.get("path", "?") if isinstance(edit, dict) else "?"
                raise ToolError(
                    f"Edit {index} of {len(edits)} ({shown}): {error} "
                    "No edit of this call was kept: send all the edits again, with this one corrected."
                ) from error
        touched = sorted({edit["path"].strip() for edit in edits})
        problems = self.check({path: self.files[path] for path in touched if path in self.files}) if self.check else []
        if problems:
            self.files = saved
            raise ToolError(
                "No edit of this call was kept: the changed code would have these errors (static check):\n"
                + "\n".join(problems)
                + "\nFix them (e.g. import a name you use, check indentation and parentheses) "
                "and send all the edits again."
            )
        return "".join(diffs)

    def diff(self) -> str:
        """Return the unified diff of every change, in path order (empty if nothing changed)."""
        return "".join(
            unified_diff(path, original, updated)
            for path, (original, updated) in sorted(self.files.items())
            if original != updated
        )

    def changed_files(self) -> dict[str, str]:
        """Return the new content of every file that really changed."""
        return {path: updated for path, (original, updated) in sorted(self.files.items()) if original != updated}

    def is_empty(self) -> bool:
        return not self.changed_files()

    def view(self) -> str:
        return self.diff() or "No change proposed yet."

    def tools(self) -> list[Tool]:
        """Return the edit tool (fixer) and the view tool (fixer and reviewer)."""
        edit_schema = object_schema(
            {
                "path": {"type": "string", "description": "File path in the repository, e.g. src/app/calc.py."},
                "old_text": {"type": "string", "description": "EXACT unique excerpt, copied from read_file(raw=true)."},
                "new_text": {"type": "string", "description": "New text replacing the excerpt."},
            }
        )
        edits_schema = object_schema(
            {
                "edits": {
                    "type": "array",
                    "description": "Every change of the fix, applied in order (several per file allowed).",
                    "items": edit_schema,
                }
            }
        )
        return [
            Tool(
                "propose_edits",
                "Propose ALL the changes of the fix in one call (all applied or none). Returns the diff. "
                "Publishes nothing: the change is reviewed.",
                edits_schema,
                self.propose_edits,
                frozenset({TAG_EDIT}),
                read_only=False,
            ),
            Tool(
                "propose_edit",
                "Propose a change to a file by replacing a unique excerpt. Publishes nothing: the change is reviewed.",
                edit_schema,
                self.propose_edit,
                frozenset({TAG_EDIT}),
                read_only=False,
            ),
            Tool(
                "view_changeset",
                "Show the diff of all the changes proposed so far.",
                object_schema({}),
                self.view,
                frozenset({TAG_EDIT, TAG_REVIEW}),
            ),
        ]
