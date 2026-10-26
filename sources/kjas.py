"""기록학연구 (The Korean Journal of Archival Studies).

Source page: https://www.koreascience.kr/journal/HGKRBZ.page?lang=ko — the
KoreaScience platform renders the latest issue's table of contents (serial /
통권 numbering). Each article has a detail page that exposes Highwire / Google
Scholar ``<meta name="citation_*">`` tags, which we use as the primary, stable
extraction path for title / author / page / DOI / abstract / full-text link.

Issue identity: serial number (통권), e.g. 제83호 -> issue_id "83",
sort_key (83,).

NOTE ON SELECTORS
-----------------
``META_*`` names follow the Highwire standard and are stable across the
KoreaScience platform. The ``SEL_*`` list-page selectors are the part most
likely to need adjustment if the markup changes; confirm them on the first live
run. Failures are logged with the exact selector tried.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .base import Article, ExtractionError, Issue, clean_text, extract_keywords, fetch_html

KEY = "kjas"
NAME = "기록학연구"
URL = "https://www.koreascience.kr/journal/HGKRBZ.page?lang=ko"
BASE = "https://www.koreascience.kr"

# --- List-page selectors / patterns (edit here when markup changes) ---------

# The block that wraps the latest issue's table of contents. Tried in order.
# ``ol.num-pad`` is the live KoreaScience TOC list; scoping to it keeps us from
# picking up navbar / "이번 주" links elsewhere on the page.
SEL_LATEST_ISSUE_BLOCK = [
    "ol.num-pad",
    "div.issue.latest",
    "div.latestIssue",
    "div.current-issue",
    "div.issueList .issue",
    "div.articleList",
]

# Anchors to individual article detail pages within the issue block / page.
# Real KoreaScience articles use the "/article/JAKO…" pattern; the bare
# "/article/" match would also catch the navbar "article-week-view" link.
SEL_ARTICLE_LINKS = [
    "a[href*='/article/JAKO']",
    "a[href*='/article/']",
    "a.articleTitle[href]",
    ".articleList a[href*='article']",
]

# Where the 통권/호수 string is rendered on the list page.
SEL_ISSUE_HEADER = [".issueTitle", ".issue-title", "h2", "h3", ".tit"]

RE_SERIAL = re.compile(r"(?:통권\s*)?제?\s*(\d+)\s*호")
RE_VOL_NO = re.compile(r"Vol\.?\s*(\d+).*?No\.?\s*(\d+)", re.IGNORECASE)
RE_DOI = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")

# --- Detail-page meta tags (Highwire standard — stable) ---------------------

META_TITLE = "citation_title"
META_AUTHOR = "citation_author"  # repeated, one per author
META_DOI = "citation_doi"
META_FIRSTPAGE = "citation_firstpage"
META_LASTPAGE = "citation_lastpage"
META_ABSTRACT = "citation_abstract"
META_PDF_URL = "citation_pdf_url"
META_HTML_URL = "citation_abstract_html_url"
META_FULLTEXT_URL = "citation_fulltext_html_url"

# Korean title on the detail page. ``citation_title`` holds the *English* title,
# but this is a Korean journal — the 국문 제목 is in the visible article heading.
SEL_TITLE_KO = ["h1.article-title", ".article-title", "h2.article-title"]

# Fallback abstract container on the detail page if the meta tag is absent.
# KoreaScience currently omits ``citation_abstract`` and renders the 초록 in a
# <p> right after a heading (h3/h4/h5) whose text is "초록"/"Abstract"; that
# heading-based lookup (see ``_extract_abstract``) is tried before these.
SEL_ABSTRACT_BODY = ["#abstractCont", ".abstractCont", ".abstract", "#abstract", ".articleAbstract"]
# Heading labels that introduce the abstract paragraph.
ABSTRACT_HEADINGS = {"초록", "국문초록", "abstract", "영문초록"}


def sort_key(issue_id: str) -> tuple[int]:
    """Map an issue_id like "83" to a comparable (serial,) tuple."""
    try:
        return (int(issue_id),)
    except (ValueError, TypeError):
        return (0,)


def _meta(soup: BeautifulSoup, name: str) -> str:
    el = soup.find("meta", attrs={"name": name})
    if el and el.get("content"):
        return clean_text(el["content"])
    return ""


def _meta_all(soup: BeautifulSoup, name: str) -> list[str]:
    return [
        clean_text(el["content"])
        for el in soup.find_all("meta", attrs={"name": name})
        if el.get("content")
    ]


def _authors(soup: BeautifulSoup) -> list[str]:
    """Authors from ``citation_author``.

    The Highwire standard repeats the tag once per author, but KoreaScience
    packs them all into a single tag joined by ';' (with a trailing ';'), e.g.
    "유샛별 ; 이성숙 ;". Handle both by splitting every tag's content on ';'.
    """
    authors: list[str] = []
    for content in _meta_all(soup, META_AUTHOR):
        for name in content.split(";"):
            name = name.strip()
            if name:
                authors.append(name)
    return authors


def _extract_abstract(soup: BeautifulSoup) -> str:
    """Find the abstract text, preferring the ``citation_abstract`` meta tag.

    Falls back to the paragraph following an abstract heading (초록/Abstract),
    then to the legacy ``SEL_ABSTRACT_BODY`` containers.
    """
    abstract = _meta(soup, META_ABSTRACT)
    if abstract:
        return abstract

    for heading in soup.find_all(["h3", "h4", "h5"]):
        if clean_text(heading.get_text()).lower() in ABSTRACT_HEADINGS:
            para = heading.find_next("p")
            if para is not None:
                text = clean_text(para.get_text(" "))
                if text:
                    return text

    for sel in SEL_ABSTRACT_BODY:
        el = soup.select_one(sel)
        if el:
            text = clean_text(el.get_text(" "))
            if text:
                return text
    return ""


def _detect_serial(soup: BeautifulSoup, logger: logging.Logger) -> tuple[str, str, tuple]:
    """Return (issue_id, issue_label, sort_key) of the latest issue."""
    texts: list[str] = []
    for sel in SEL_ISSUE_HEADER:
        for el in soup.select(sel):
            texts.append(el.get_text(" ", strip=True))
    texts.append(soup.title.get_text() if soup.title else "")

    for text in texts:
        m = RE_SERIAL.search(text or "")
        if m:
            serial = int(m.group(1))
            issue_id = str(serial)
            label = f"통권 제{serial}호"
            logger.debug("kjas: detected serial issue %s from %r", label, text[:60])
            return issue_id, label, (serial,)

    raise ExtractionError(
        "kjas: could not detect the serial issue number (제N호). "
        "Check SEL_ISSUE_HEADER / RE_SERIAL."
    )


def _collect_article_links(soup: BeautifulSoup, logger: logging.Logger) -> list[str]:
    """Collect detail-page URLs for the latest issue, de-duplicated, in order."""
    scope = None
    for sel in SEL_LATEST_ISSUE_BLOCK:
        scope = soup.select_one(sel)
        if scope is not None:
            logger.debug("kjas: latest-issue block matched via %r", sel)
            break
    search_root = scope if scope is not None else soup

    links: list[str] = []
    seen: set[str] = set()
    for sel in SEL_ARTICLE_LINKS:
        for a in search_root.select(sel):
            href = a.get("href")
            if not href:
                continue
            full = urljoin(BASE, href)
            if full not in seen:
                seen.add(full)
                links.append(full)
        if links:
            break

    if not links:
        raise ExtractionError(
            "kjas: no article detail links found; tried "
            f"{SEL_ARTICLE_LINKS}. Page markup likely changed."
        )
    logger.debug("kjas: found %d article links", len(links))
    return links


def _parse_detail(session, url: str, logger: logging.Logger) -> Article | None:
    html = fetch_html(session, url, logger)
    soup = BeautifulSoup(html, "lxml")

    # Prefer the Korean title from the visible heading; citation_title is English.
    title = ""
    for sel in SEL_TITLE_KO:
        el = soup.select_one(sel)
        if el:
            title = clean_text(el.get_text(" "))
            if title:
                break
    if not title:
        title = _meta(soup, META_TITLE)   # fall back to (English) meta
    if not title:
        logger.warning("kjas: no title found on %s — skipping", url)
        return None

    authors = _authors(soup)

    first = _meta(soup, META_FIRSTPAGE)
    last = _meta(soup, META_LASTPAGE)
    if first and last:
        pages = f"{first}-{last}"
    else:
        pages = first or last or ""

    doi = _meta(soup, META_DOI)
    if not doi:
        dm = RE_DOI.search(html)
        doi = dm.group(0) if dm else ""

    abstract = _extract_abstract(soup)
    if not abstract:
        logger.warning("kjas: no abstract found on %s", url)

    # Prefer a human-facing page; fall back to the PDF or the detail URL itself.
    fulltext = _meta(soup, META_HTML_URL) or _meta(soup, META_FULLTEXT_URL) or _meta(soup, META_PDF_URL) or url

    return Article(
        title=title,
        authors=authors,
        pages=pages,
        doi=doi,
        abstract=abstract,
        url=fulltext,
        keywords=extract_keywords(soup),
    )


def _issue_from_listing(session, soup: BeautifulSoup, source_url: str, logger: logging.Logger) -> Issue:
    """Parse a list page (latest or archive), fetching each article detail page.

    A single article whose detail page cannot be fetched/parsed is skipped (with
    a warning) rather than failing the whole issue — important when backfilling
    many issues against a flaky host.
    """
    issue_id, issue_label, skey = _detect_serial(soup, logger)
    links = _collect_article_links(soup, logger)

    articles: list[Article] = []
    for link in links:
        try:
            art = _parse_detail(session, link, logger)
        except ExtractionError as exc:
            logger.warning("kjas: skipping article (fetch failed) %s: %s", link, exc)
            continue
        if art is not None:
            articles.append(art)

    if not articles:
        raise ExtractionError("kjas: issue detected but no articles parsed.")

    logger.info("kjas: parsed issue %s with %d articles", issue_label, len(articles))
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
    """Fetch the list page, then each article detail page, into an :class:`Issue`."""
    logger.info("kjas: fetching %s", URL)
    html = fetch_html(session, URL, logger)
    soup = BeautifulSoup(html, "lxml")
    return _issue_from_listing(session, soup, URL, logger)


def fetch_issue(session, url: str, logger: logging.Logger) -> Issue:
    """Fetch one archive issue page (e.g. /journal/HGKRBZ/n87.page) into an :class:`Issue`."""
    logger.info("kjas: fetching %s", url)
    html = fetch_html(session, url, logger)
    soup = BeautifulSoup(html, "lxml")
    return _issue_from_listing(session, soup, url, logger)


# Past-issue links on the journal page look like /journal/HGKRBZ/n87.page?lang=ko.
RE_ISSUE_HREF = re.compile(r"/journal/HGKRBZ/n(\d+)\.page")


def issue_id_from_url(url: str) -> str | None:
    """Derive the issue_id (e.g. "87") from a past-issue URL, or None."""
    m = RE_ISSUE_HREF.search(url)
    return str(int(m.group(1))) if m else None


def list_issue_urls(session, logger: logging.Logger, limit: int | None = None) -> list[str]:
    """Return past-issue list-page URLs from the journal page, newest first."""
    logger.info("kjas: listing issues from %s", URL)
    html = fetch_html(session, URL, logger)
    soup = BeautifulSoup(html, "lxml")

    refs: list[tuple[int, str]] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        m = RE_ISSUE_HREF.search(a["href"])
        if m:
            full = urljoin(BASE, a["href"])
            if full not in seen:
                seen.add(full)
                refs.append((int(m.group(1)), full))
    refs.sort(key=lambda r: r[0], reverse=True)  # newest serial first
    urls = [u for _, u in refs]
    logger.info("kjas: found %d past issues", len(urls))
    return urls[:limit] if limit else urls
