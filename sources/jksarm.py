"""한국기록관리학회지 (Journal of Korean Society of Archives and Records Management).

Source page: https://accesson.kr/jksarm/  — the AccessON platform renders the
*latest* issue (volume-number system) directly on the journal landing page,
including the abstract of each article, so a single fetch is enough.

Issue identity: volume + number, e.g. 제27권 제2호 -> issue_id "27-2",
sort_key (27, 2).

NOTE ON SELECTORS
-----------------
The constants in the ``SEL_*`` / ``RE_*`` blocks below are the single place to
adjust extraction when the page markup changes. They are written with several
fallbacks because AccessON occasionally tweaks its templates. On the first live
run, confirm each field is populated; an empty field is logged loudly and a
missing issue header raises :class:`ExtractionError`.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import Article, ExtractionError, Issue, clean_text, fetch_html

KEY = "jksarm"
NAME = "한국기록관리학회지"
URL = "https://accesson.kr/jksarm/"
BASE = "https://accesson.kr"  # for resolving relative detail links

# --- Selectors / patterns (edit here when markup changes) -------------------
#
# Confirmed against the live AccessON markup (제26권 제2호). The platform renders
# the latest issue's table of contents — including each abstract — directly on
# the landing page, so a single fetch is enough. The legacy generic selectors
# are kept as trailing fallbacks in case AccessON tweaks its template again.

# Candidate containers that wrap a single article in the latest-issue list.
# Tried in order; the first selector that returns elements wins.
SEL_ARTICLE_ITEMS = [
    "div.issue_article",          # live AccessON layout
    "div.article-list li",
    "ul.article-list > li",
    "div.articleList li",
    "li.article-item",
    "div.list-article > div",
]

# Within one article item, candidate selectors per field.
SEL_TITLE = ["a.ia_title", "a.title", ".article-title a", ".tit a", "a.tit", ".title"]
SEL_AUTHORS = [".author", ".authors", ".article-author", ".writer"]
SEL_PAGES = [".page", ".pages", ".pp", ".article-page"]
# Authors and pages share one unclassed <span> in ``div.il_bottom`` on AccessON,
# e.g. "유샛별 ; 이성숙 pp.19-46"; parsed via SEL_META_SPAN + RE_PAGES_LABEL below.
SEL_META_SPAN = ["div.il_bottom > span", ".il_bottom span"]
# First paragraph of ``.list_abstract`` is the Korean 초록 (an English Abstract
# follows in a second <p>); we copy the Korean abstract verbatim.
SEL_ABSTRACT = [
    ".list_abstract p.mgb_20",
    ".list_abstract p",
    ".list_abstract",
    ".abstract",
    ".article-abstract",
    ".abs",
    "p.abstract",
]
SEL_LINK = ["a.ia_title", "a.title", ".article-title a", "a.tit", "a[href]"]
SEL_DOI = ["a.doi_link", "a[href*='doi.org']", ".doi", "a.doi"]

# Issue header (권/호) — searched across the whole page.
SEL_ISSUE_HEADER = [
    "div.issue_title",            # live AccessON layout ("26권 2호 …")
    ".current-issue .issue-info",
    ".issue-title",
    ".vol-info",
    "h2",
    "h3",
]

# Regex helpers.
RE_VOL_NO = re.compile(r"제?\s*(\d+)\s*권\s*제?\s*(\d+)\s*호")
RE_DOI = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
RE_PAGES = re.compile(r"(\d+)\s*[-~–]\s*(\d+)")
# Matches the "pp.19-46" page label so it can be stripped to leave author names.
RE_PAGES_LABEL = re.compile(r"pp\.?\s*\d+\s*[-~–]\s*\d+", re.IGNORECASE)


def sort_key(issue_id: str) -> tuple[int, int]:
    """Map an issue_id like "27-2" to a comparable (volume, number) tuple."""
    try:
        vol, no = issue_id.split("-", 1)
        return (int(vol), int(no))
    except (ValueError, AttributeError):
        return (0, 0)


def _first(node, selectors: list[str]):
    """Return the first element matching any of ``selectors`` (or None)."""
    for sel in selectors:
        found = node.select_one(sel)
        if found is not None:
            return found
    return None


def _select_items(soup: BeautifulSoup, logger: logging.Logger):
    for sel in SEL_ARTICLE_ITEMS:
        items = soup.select(sel)
        if items:
            logger.debug("jksarm: matched %d article items via %r", len(items), sel)
            return items
    raise ExtractionError(
        "jksarm: no article items found; tried selectors "
        f"{SEL_ARTICLE_ITEMS}. Page markup likely changed."
    )


def _parse_issue_label(soup: BeautifulSoup, logger: logging.Logger) -> tuple[str, str, tuple]:
    """Return (issue_id, issue_label, sort_key) for the rendered latest issue."""
    # Try dedicated header elements first, then fall back to scanning page text.
    candidates: list[str] = []
    for sel in SEL_ISSUE_HEADER:
        for el in soup.select(sel):
            candidates.append(el.get_text(" ", strip=True))
    candidates.append(soup.get_text(" ", strip=True))

    for text in candidates:
        m = RE_VOL_NO.search(text or "")
        if m:
            vol, no = int(m.group(1)), int(m.group(2))
            issue_id = f"{vol}-{no}"
            label = f"제{vol}권 제{no}호"
            logger.debug("jksarm: detected issue %s", label)
            return issue_id, label, (vol, no)

    raise ExtractionError(
        "jksarm: could not detect the volume/number header (제N권 제M호). "
        "Check SEL_ISSUE_HEADER / RE_VOL_NO."
    )


def _extract_abstract(item) -> str:
    """Return the article abstract from the AccessON ``.list_abstract`` block.

    The block holds the Korean 초록 then the English Abstract, each in its own
    <p>. Some records leave the Korean <p> empty and put the (still Korean) text
    under the "Abstract" heading instead, so we take the first *non-empty*
    paragraph rather than a fixed position, then fall back to SEL_ABSTRACT.
    """
    box = item.select_one(".list_abstract") or item.select_one(".abstract_box")
    if box is not None:
        for para in box.find_all("p"):
            text = clean_text(para.get_text(" "))
            if text:
                return text

    abstract_el = _first(item, SEL_ABSTRACT)
    return clean_text(abstract_el.get_text(" ")) if abstract_el else ""


def _parse_article(item, logger: logging.Logger) -> Article | None:
    title_el = _first(item, SEL_TITLE)
    title = clean_text(title_el.get_text()) if title_el else ""
    if not title:
        logger.warning("jksarm: skipping an item with no title (selectors %s)", SEL_TITLE)
        return None

    # Link (prefer the title anchor, then any link). Resolve to an absolute URL.
    link = ""
    link_el = title_el if (title_el and title_el.name == "a") else _first(item, SEL_LINK)
    if link_el is not None and link_el.has_attr("href"):
        link = urljoin(BASE, link_el["href"])

    # Authors and pages: prefer dedicated elements; otherwise fall back to the
    # combined "<authors> pp.<first>-<last>" span used by AccessON.
    authors_el = _first(item, SEL_AUTHORS)
    pages_el = _first(item, SEL_PAGES)
    meta_text = ""
    if authors_el is None or pages_el is None:
        meta_el = _first(item, SEL_META_SPAN)
        meta_text = clean_text(meta_el.get_text()) if meta_el else ""

    pages = ""
    if pages_el is not None:
        pm = RE_PAGES.search(clean_text(pages_el.get_text()))
        pages = f"{pm.group(1)}-{pm.group(2)}" if pm else clean_text(pages_el.get_text())
    elif meta_text:
        pm = RE_PAGES.search(meta_text)
        pages = f"{pm.group(1)}-{pm.group(2)}" if pm else ""

    if authors_el is not None:
        authors_raw = clean_text(authors_el.get_text())
    else:
        # Drop the trailing "pp.19-46" label, leaving just the author names.
        authors_raw = clean_text(RE_PAGES_LABEL.sub("", meta_text))
    authors = [a.strip() for a in re.split(r"[,;·]", authors_raw) if a.strip()]

    # DOI: explicit element/link first, else scan the item text.
    doi = ""
    doi_el = _first(item, SEL_DOI)
    if doi_el is not None:
        href = doi_el.get("href", "")
        dm = RE_DOI.search(href) or RE_DOI.search(clean_text(doi_el.get_text()))
        doi = dm.group(0) if dm else ""
    if not doi:
        dm = RE_DOI.search(item.get_text(" ", strip=True))
        doi = dm.group(0) if dm else ""

    abstract = _extract_abstract(item)

    if not abstract:
        logger.warning("jksarm: no abstract for %r (selectors %s)", title[:40], SEL_ABSTRACT)

    return Article(
        title=title,
        authors=authors,
        pages=pages,
        doi=doi,
        abstract=abstract,
        url=link,
    )


def _issue_from_soup(soup: BeautifulSoup, source_url: str, logger: logging.Logger) -> Issue:
    """Parse a rendered issue page (landing or archive) into an :class:`Issue`."""
    issue_id, issue_label, skey = _parse_issue_label(soup, logger)
    items = _select_items(soup, logger)

    articles: list[Article] = []
    for item in items:
        art = _parse_article(item, logger)
        if art is not None:
            articles.append(art)

    if not articles:
        raise ExtractionError("jksarm: issue detected but no articles parsed.")

    logger.info("jksarm: parsed issue %s with %d articles", issue_label, len(articles))
    return Issue(
        journal_key=KEY,
        journal_name=NAME,
        issue_id=issue_id,
        issue_label=issue_label,
        sort_key=skey,
        source_url=source_url,
        articles=articles,
    )


def fetch_latest_issue(session, logger: logging.Logger) -> Issue:
    """Fetch the landing page and parse the latest issue into an :class:`Issue`."""
    logger.info("jksarm: fetching %s", URL)
    html = fetch_html(session, URL, logger)
    soup = BeautifulSoup(html, "lxml")
    return _issue_from_soup(soup, URL, logger)


def fetch_issue(session, url: str, logger: logging.Logger) -> Issue:
    """Fetch one archive issue page (e.g. /jksarm/v.26/1/2026) into an :class:`Issue`."""
    logger.info("jksarm: fetching %s", url)
    html = fetch_html(session, url, logger)
    soup = BeautifulSoup(html, "lxml")
    return _issue_from_soup(soup, url, logger)


# Archive issue links on the landing page look like /jksarm/v.26/2/2026.
RE_ISSUE_HREF = re.compile(r"^/jksarm/v\.\d+/\d+/\d+/?$")
RE_ISSUE_ID_URL = re.compile(r"/v\.(\d+)/(\d+)/")


def issue_id_from_url(url: str) -> str | None:
    """Derive the issue_id (e.g. "26-2") from an archive URL, or None."""
    m = RE_ISSUE_ID_URL.search(url)
    return f"{int(m.group(1))}-{int(m.group(2))}" if m else None


def list_issue_urls(session, logger: logging.Logger, limit: int | None = None) -> list[str]:
    """Return archive issue URLs from the landing page, newest first."""
    logger.info("jksarm: listing issues from %s", URL)
    html = fetch_html(session, URL, logger)
    soup = BeautifulSoup(html, "lxml")

    urls: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if RE_ISSUE_HREF.match(href):
            full = urljoin(BASE, href)
            if full not in seen:
                seen.add(full)
                urls.append(full)
    logger.info("jksarm: found %d archive issues", len(urls))
    return urls[:limit] if limit else urls
