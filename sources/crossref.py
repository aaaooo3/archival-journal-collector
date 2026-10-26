"""International journals via the free Crossref REST API (no API key).

Each journal is configured by ISSN. We fetch its most recent issue's articles
(title / authors / pages / DOI / abstract-if-deposited / link) from Crossref,
which returns clean structured JSON for virtually any journal — far more robust
than scraping each publisher's bespoke HTML.

NOTE: international journals are frequently paywalled, so abstracts may be
missing (publishers don't always deposit them to Crossref) and full-text PDFs
usually aren't freely downloadable — i.e. the summariser may not work on them.
Bibliographic metadata, however, is reliable.

Adding another journal: append a ``CrossrefSource(key, name, issn)`` in
``sources/__init__.py``.
"""

from __future__ import annotations

import logging
import re
import time

from .base import Article, ExtractionError, Issue, clean_keywords, clean_text

API = "https://api.crossref.org"
REQUEST_DELAY_SECONDS = 1.0
MAX_RETRIES = 4

# Fields we ask Crossref to return (smaller, faster responses).
_SELECT = "title,author,page,DOI,abstract,URL,volume,issue,published,published-print,published-online,issued"


def _strip_jats(text: str) -> str:
    """Crossref abstracts are JATS XML (<jats:p>…</jats:p>); strip tags."""
    if not text:
        return ""
    return clean_text(re.sub(r"<[^>]+>", " ", text))


def _get_json(session, url: str, logger: logging.Logger) -> dict:
    last: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.debug("GET %s (attempt %d/%d)", url, attempt, MAX_RETRIES)
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            time.sleep(REQUEST_DELAY_SECONDS)
            return data
        except Exception as exc:  # noqa: BLE001
            last = exc
            backoff = 2 ** attempt
            logger.warning("Crossref fetch failed for %s (%s); retrying in %ds", url, exc, backoff)
            time.sleep(backoff)
    raise ExtractionError(f"Crossref request failed after {MAX_RETRIES} attempts: {url}: {last}")


def _year_month(item: dict) -> tuple[int, int]:
    for key in ("published", "published-print", "published-online", "issued"):
        node = item.get(key)
        parts = node.get("date-parts") if isinstance(node, dict) else None
        if parts and parts[0] and parts[0][0]:
            dp = parts[0]
            return int(dp[0]), (int(dp[1]) if len(dp) > 1 else 0)
    return 0, 0


def _article_from_item(item: dict) -> Article | None:
    title = (item.get("title") or [""])[0]
    if not title:
        return None
    authors: list[str] = []
    for a in item.get("author", []):
        name = " ".join(p for p in (a.get("given"), a.get("family")) if p).strip()
        if not name:
            name = a.get("name", "")
        if name:
            authors.append(name)
    doi = item.get("DOI", "")
    subjects = [s for s in (item.get("subject") or []) if isinstance(s, str) and s.strip()]
    return Article(
        title=clean_text(title),
        authors=authors,
        pages=clean_text(item.get("page", "")),
        doi=doi,
        abstract=_strip_jats(item.get("abstract", "")),
        url=item.get("URL", "") or (f"https://doi.org/{doi}" if doi else ""),
        keywords=clean_keywords(subjects),
    )


class CrossrefSource:
    """A journal source backed by Crossref, identified by ISSN.

    Exposes the same duck-typed interface as the scraping modules
    (``KEY``/``NAME``/``URL``, ``fetch_latest_issue``, ``sort_key``,
    plus ``list_issue_urls``/``fetch_issue``/``issue_id_from_url`` for backfill).
    """

    def __init__(self, key: str, name: str, issn: str):
        self.KEY = key
        self.NAME = name
        self.ISSN = issn
        self.URL = f"https://portal.issn.org/resource/ISSN/{issn}"

    # --- ordering ---------------------------------------------------------
    def sort_key(self, issue_id: str) -> tuple:
        nums = tuple(int(n) for n in re.findall(r"\d+", issue_id or ""))
        return nums or (0,)

    @staticmethod
    def _issue_of(item: dict) -> tuple[str, str]:
        return (str(item.get("volume", "")).strip(), str(item.get("issue", "")).strip())

    @staticmethod
    def _issue_id(vol: str, iss: str) -> str:
        if vol and iss:
            return f"{vol}-{iss}"
        return vol or iss or "0"   # journals like Archivaria number by issue only

    @staticmethod
    def _issue_label(vol: str, iss: str) -> str:
        if vol and iss:
            return f"Vol. {vol}, No. {iss}"
        if vol:
            return f"Vol. {vol}"
        if iss:
            return f"No. {iss}"
        return "(latest)"

    # --- fetching ---------------------------------------------------------
    def _recent_items(self, session, logger: logging.Logger, rows: int = 80) -> list[dict]:
        url = (f"{API}/journals/{self.ISSN}/works"
               f"?filter=type:journal-article&sort=published&order=desc&rows={rows}&select={_SELECT}")
        items = _get_json(session, url, logger).get("message", {}).get("items", [])
        if not items:
            raise ExtractionError(f"{self.KEY}: Crossref returned no works for ISSN {self.ISSN}")
        return items

    def _build_issue(self, items: list[dict], vol: str, iss: str, logger: logging.Logger) -> Issue:
        articles = [a for a in (_article_from_item(it) for it in items) if a is not None]
        if not articles:
            raise ExtractionError(f"{self.KEY}: no parseable articles for vol {vol!r} issue {iss!r}")
        year, month = _year_month(items[0])
        published = f"{year:04d}-{month:02d}" if year and month else (f"{year:04d}" if year else "")
        issue_id = self._issue_id(vol, iss)
        label = self._issue_label(vol, iss)
        skey = self.sort_key(issue_id)   # keep consistent with sort_key(issue_id)
        logger.info("%s: issue %s with %d articles", self.KEY, label, len(articles))
        return Issue(
            journal_key=self.KEY, journal_name=self.NAME,
            issue_id=issue_id, issue_label=label,
            sort_key=skey, source_url=self.URL, articles=articles, published=published,
        )

    def fetch_latest_issue(self, session, logger: logging.Logger) -> Issue:
        logger.info("%s: fetching latest from Crossref (ISSN %s)", self.KEY, self.ISSN)
        items = self._recent_items(session, logger)
        vol, iss = self._issue_of(items[0])
        in_issue = [it for it in items if self._issue_of(it) == (vol, iss)]
        return self._build_issue(in_issue, vol, iss, logger)

    # --- backfill support -------------------------------------------------
    def list_issue_urls(self, session, logger: logging.Logger, limit: int | None = None) -> list[str]:
        items = self._recent_items(session, logger, rows=200)
        out: list[str] = []
        seen: set[tuple[str, str]] = set()
        for it in items:
            vi = self._issue_of(it)
            if vi not in seen:
                seen.add(vi)
                out.append(f"{self.KEY}|{vi[0]}|{vi[1]}")
        logger.info("%s: %d issues available (recent)", self.KEY, len(out))
        return out[:limit] if limit else out

    def issue_id_from_url(self, url: str) -> str | None:
        parts = url.split("|")
        return self._issue_id(parts[1], parts[2]) if len(parts) == 3 else None

    def fetch_issue(self, session, url: str, logger: logging.Logger) -> Issue:
        parts = url.split("|")
        if len(parts) != 3:
            raise ExtractionError(f"{self.KEY}: bad issue ref {url!r}")
        vol, iss = parts[1], parts[2]
        items = self._recent_items(session, logger, rows=200)
        in_issue = [it for it in items if self._issue_of(it) == (vol, iss)]
        return self._build_issue(in_issue, vol, iss, logger)
