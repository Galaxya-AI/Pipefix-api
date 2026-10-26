"""Failure notifier: tells humans when a run did not fix the pipeline.

Two optional channels: a GitLab issue per failing branch (opened by the first
failure, commented by the next ones; never in dry-run mode) and a Slack/Teams
webhook message. A notification error never makes the run fail: it is only journaled.
"""

from __future__ import annotations

import os
from typing import Any

import requests

from connectors.errors.errors import ConnectorError
from journal.run_journal import RunJournal
from workflow_publication.publication_texts import failure_issue_title, failure_report_text
from workflow_remediation.ports import CodePlatform

NOTIFY_ON_FAILURE = os.getenv("NOTIFY_ON_FAILURE", "true").strip().lower() in ("1", "true", "yes")

FAILURE_ISSUE_LABEL = os.getenv("FAILURE_ISSUE_LABEL", "ci-remediation")

# Empty: no chat message
CHAT_WEBHOOK_URL = os.getenv("CHAT_WEBHOOK_URL", "")

FAILURE_STATUSES = frozenset(
    {
        "not_fixable_by_code",
        "infra_flaky",
        "no_failed_jobs",
        "no_change_proposed",
        "review_rejected",
        "max_rounds_reached",
        "pipeline_timeout",
        "blocked_by_policy",
        "error",
    }
)


def needs_notification(status: str) -> bool:
    """True for a known failure status or a failed agent ("<agent>_failed")."""
    return status in FAILURE_STATUSES or status.endswith("_failed")


class FailureNotifier:
    def __init__(
        self,
        platform: CodePlatform,
        journal: RunJournal,
        enabled: bool = NOTIFY_ON_FAILURE,
        label: str = FAILURE_ISSUE_LABEL,
        chat_webhook_url: str = CHAT_WEBHOOK_URL,
    ) -> None:
        self.platform = platform
        self.journal = journal
        self.enabled = enabled
        self.label = label
        self.chat_webhook_url = chat_webhook_url

    def notify(self, project: str, report: dict[str, Any], dry_run: bool) -> dict[str, Any]:
        """Return what was sent, {"issue": url or None, "chat": bool}, or {} if no notification was needed.

        In dry-run mode no issue is opened, but the chat message is still sent.
        """
        if not self.enabled or not needs_notification(str(report.get("status", ""))):
            return {}
        text = failure_report_text(report)
        branch = str((report.get("pipeline") or {}).get("branch") or "unknown branch")
        issue_url = None if dry_run else self.report_in_issue(project, branch, text)
        chat_sent = self.send_chat(f"[{project}] {failure_issue_title(branch)}\n\n{text}")
        return {"issue": issue_url, "chat": chat_sent}

    def report_in_issue(self, project: str, branch: str, text: str) -> str | None:
        """Open the branch's issue, or comment it if already open; None if GitLab refused (e.g. no api scope)."""
        title = failure_issue_title(branch)
        try:
            existing: dict[str, Any] | None = next(
                (issue for issue in self.platform.list_open_issues(project, self.label) if issue.get("title") == title),
                None,
            )
            if existing is not None:
                self.platform.add_issue_note(project, int(existing["iid"]), text)
                self.journal.log("notifier", "issue_commented", {"url": existing.get("web_url")})
                return str(existing.get("web_url"))
            created = self.platform.create_issue(project, title, text, [self.label])
            self.journal.log("notifier", "issue_opened", {"url": created.get("web_url")})
            return str(created.get("web_url"))
        except ConnectorError as error:
            self.journal.log("notifier", "issue_failed", {"error": str(error)})
            return None

    def send_chat(self, text: str) -> bool:
        """Return True if the webhook accepted the message; False when no webhook is configured."""
        if not self.chat_webhook_url:
            return False
        try:
            # Slack and Teams incoming webhooks both accept a JSON {"text": ...} body
            response = requests.post(self.chat_webhook_url, json={"text": text}, timeout=10)
        except requests.RequestException as error:
            self.journal.log("notifier", "chat_failed", {"error": str(error)})
            return False
        sent = response.status_code < 400
        self.journal.log("notifier", "chat_sent" if sent else "chat_failed", {"status": response.status_code})
        return sent
