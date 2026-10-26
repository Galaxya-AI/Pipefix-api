"""Web search connector, used by the research agent.

Searches Google through Serper (serper.dev): POST /search with header X-API-KEY and
body {"q": ..., "num": ...}. Reads public pages as plain text.
"""

from __future__ import annotations

import html
import os
import re
from typing import Any, ClassVar

import requests

from connectors.base.base import Connector
from connectors.http_client.http_api import HttpApi
from text_tools.log_cleaning import truncate
from tools.errors import ToolError
from tools.tags import TAG_RESEARCH
from tools.tool import Tool, object_schema

SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
SERPER_URL = os.getenv("SERPER_URL", "https://google.serper.dev")

MAX_RESULTS = 20
MAX_PAGE_CHARS = 8000

# Removed with their content
SCRIPT_PATTERN = re.compile(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>")
TAG_PATTERN = re.compile(r"(?s)<[^>]+>")
SPACE_PATTERN = re.compile(r"[ \t]+|\n\s*\n+")


def html_to_text(page: str) -> str:
    text = SCRIPT_PATTERN.sub(" ", page)
    text = TAG_PATTERN.sub(" ", text)
    text = html.unescape(text)
    return SPACE_PATTERN.sub(lambda match: "\n" if "\n" in match.group(0) else " ", text).strip()


class WebSearchConnector(Connector):
    NAME: ClassVar[str] = "web_search"
    DESCRIPTION: ClassVar[str] = "Google web search (Serper) and public page reading."

    def __init__(self, api: HttpApi) -> None:
        self.api = api
        # Separate session for public pages: the API key must never be sent to other sites.
        self.page_session = requests.Session()

    @classmethod
    def from_environment(cls) -> WebSearchConnector | None:
        if not SERPER_API_KEY:
            return None
        return cls(HttpApi(SERPER_URL, {"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"}))

    def web_search(self, query: str, count: int = 5) -> list[dict[str, Any]]:
        """Search Google through Serper; count is clamped to 1..MAX_RESULTS."""
        size = max(1, min(int(count), MAX_RESULTS))
        data = self.api.request("POST", "/search", json_body={"q": query, "num": size}).json()
        # Organic results only, without ads
        results = list(data.get("organic") or [])[:size]
        return [
            {"title": item.get("title"), "url": item.get("link"), "description": item.get("snippet")}
            for item in results
        ]

    def read_web_page(self, url: str) -> str:
        """Return a public page as plain text cut to MAX_PAGE_CHARS; raise ToolError if it cannot be read."""
        if not url.startswith(("http://", "https://")):
            raise ToolError("Only http(s) URLs can be read.")
        try:
            response = self.page_session.get(url, timeout=self.api.timeout_s)
        except requests.RequestException as error:
            raise ToolError(f"Cannot read {url}: {error}") from error
        if response.status_code >= 400:
            raise ToolError(f"Cannot read {url}: HTTP {response.status_code}")
        return truncate(html_to_text(response.text), MAX_PAGE_CHARS)

    def tools(self) -> list[Tool]:
        tags = frozenset({TAG_RESEARCH})
        return [
            Tool(
                "web_search",
                "Search the web. Use the exact error message and the library name and version.",
                object_schema(
                    {
                        "query": {"type": "string", "description": "Search query."},
                        "count": {"type": "integer", "description": "Number of results (1 to 20)."},
                    },
                    required=["query"],
                ),
                self.web_search,
                tags,
            ),
            Tool(
                "read_web_page",
                "Read a public web page as plain text.",
                object_schema({"url": {"type": "string", "description": "Page address."}}),
                self.read_web_page,
                tags,
            ),
        ]
