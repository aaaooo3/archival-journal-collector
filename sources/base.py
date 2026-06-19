"""Shared building blocks for journal sources.

This module holds the HTTP/session helpers, the data model (:class:`Article`,
:class:`Issue`) and the :class:`ExtractionError` used to signal that a parser
could not find required data (i.e. the page structure changed and selectors
need to be reviewed).

Each concrete source (``sources/jksarm.py``, ``sources/kjas.py``) keeps its own
CSS selectors as module-level constants and exposes:

    KEY          : str   - short stable id (used in state.json + filenames)
    NAME         : str   - human readable journal name
    URL          : str   - the public page that lists the latest issue
    fetch_latest_issue(session, logger) -> Issue
    sort_key(issue_id: str) -> tuple   - ordering used to detect "newer" issues
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import requests

# --- Polite-client configuration -------------------------------------------

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 30          # seconds, per request
REQUEST_DELAY_SECONDS = 2.5   # politeness pause AFTER each successful request (spec: 2~3s)
MAX_RETRIES = 4               # network retries with exponential backoff

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
}


class ExtractionError(Exception):
    """A parser could not find required data.

    Raised with a clear message so the log points at exactly which selector /
    field failed, which usually means the upstream HTML changed.
    """


# --- Data model -------------------------------------------------------------


@dataclass
class Article:
    title: str
    authors: list[str] = field(default_factory=list)
    pages: str = ""
    doi: str = ""
    abstract: str = ""
    url: str = ""  # full-text / detail link


@dataclass
class Issue:
    journal_key: str          # e.g. "jksarm"
    journal_name: str         # e.g. "한국기록관리학회지"
    issue_id: str             # canonical id for state + filename, e.g. "27-2" or "83"
    issue_label: str          # human label, e.g. "제27권 제2호" / "통권 제83호"
    sort_key: tuple           # used to compare which issue is newer
    source_url: str
    articles: list[Article] = field(default_factory=list)


# --- HTTP helpers -----------------------------------------------------------


def make_session() -> requests.Session:
    """Build a requests session with the polite default headers."""
    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)
    return session


def fetch_html(
    session: requests.Session,
    url: str,
    logger: logging.Logger,
    *,
    delay: float = REQUEST_DELAY_SECONDS,
) -> str:
    """GET ``url`` and return decoded HTML text.

    Retries transient network errors with exponential backoff (2s, 4s, 8s, 16s)
    and sleeps ``delay`` seconds after a successful fetch to stay polite.
    Raises :class:`ExtractionError` if every attempt fails.
    """
    last_exc: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.debug("GET %s (attempt %d/%d)", url, attempt, MAX_RETRIES)
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            # Korean academic sites are utf-8 but sometimes mislabel the body;
            # fall back to chardet's guess when the header looks like the default.
            if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
                resp.encoding = resp.apparent_encoding
            time.sleep(delay)
            return resp.text
        except requests.RequestException as exc:
            last_exc = exc
            backoff = 2 ** attempt
            logger.warning(
                "Fetch failed for %s (%s); retrying in %ds", url, exc, backoff
            )
            time.sleep(backoff)
    raise ExtractionError(
        f"Failed to fetch {url} after {MAX_RETRIES} attempts: {last_exc}"
    )


def clean_text(value: str | None) -> str:
    """Collapse whitespace and strip; return '' for None."""
    if not value:
        return ""
    return " ".join(value.split()).strip()
