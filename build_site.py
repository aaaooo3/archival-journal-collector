#!/usr/bin/env python3
"""Build a single-page static site from the collected Markdown digests.

Reads ``output/<key>_<id>.md`` (the stable format emitted by ``render.py``) and
writes a self-contained ``site/index.html`` — inline CSS/JS, data embedded as
JSON, no external dependencies — so it works opened locally and on GitHub Pages.

The page lists every collected issue of both journals with a journal filter and
a free-text search over title / authors / abstract.

Usage
-----
    python build_site.py                       # output/ -> site/
    python build_site.py --output out --site www

``watcher.py`` calls :func:`build_site` automatically after each run.
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import re
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"
SITE_DIR = ROOT / "site"

# Human labels for the journal-filter buttons, keyed by the file-name prefix.
JOURNAL_ORDER = ["jksarm", "kjas"]

# --- Markdown parsing (our own stable render.py format) ---------------------

_RE_HEADER = re.compile(r"^#\s+(.*?)\s+—\s+(.*)$")
_RE_META = re.compile(r"^-\s+\*\*(.+?)\*\*:\s*(.*)$")
_RE_ISSUE_ID = re.compile(r"\(`(.+?)`\)\s*$")
_RE_ARTICLE = re.compile(r"^###\s+\d+\.\s+(.*)$")
_RE_DOI_LINK = re.compile(r"\[(.+?)\]\((.+?)\)")


def _parse_markdown(text: str, journal_key: str) -> dict | None:
    """Parse one issue digest into a structured dict (or None if unparseable)."""
    lines = text.splitlines()
    if not lines:
        return None

    issue: dict = {
        "journal_key": journal_key,
        "journal_name": "",
        "issue_id": "",
        "issue_label": "",
        "source_url": "",
        "collected": "",
        "articles": [],
    }

    m = _RE_HEADER.match(lines[0])
    if m:
        issue["journal_name"], issue["issue_label"] = m.group(1).strip(), m.group(2).strip()

    # Header meta block (before the first article).
    for line in lines[1:]:
        if line.startswith("### "):
            break
        mm = _RE_META.match(line)
        if not mm:
            continue
        key, val = mm.group(1).strip(), mm.group(2).strip()
        if key == "학회지":
            issue["journal_name"] = val or issue["journal_name"]
        elif key == "호":
            idm = _RE_ISSUE_ID.search(val)
            if idm:
                issue["issue_id"] = idm.group(1)
            issue["issue_label"] = _RE_ISSUE_ID.sub("", val).strip() or issue["issue_label"]
        elif key == "출처":
            issue["source_url"] = val
        elif key == "수집일":
            issue["collected"] = val

    # Article blocks: split on the "### N. title" headings.
    article: dict | None = None
    in_abstract = False
    abstract_lines: list[str] = []

    def _flush() -> None:
        if article is not None:
            article["abstract"] = " ".join(abstract_lines).strip()
            issue["articles"].append(article)

    for line in lines:
        am = _RE_ARTICLE.match(line)
        if am:
            _flush()
            article = {"title": am.group(1).strip(), "authors": [], "pages": "",
                       "doi": "", "url": "", "abstract": ""}
            in_abstract = False
            abstract_lines = []
            continue
        if article is None:
            continue
        if line.strip() == "**초록**":
            in_abstract = True
            continue
        if line.strip() == "---":
            in_abstract = False
            continue
        mm = _RE_META.match(line)
        if mm:
            key, val = mm.group(1).strip(), mm.group(2).strip()
            if key == "저자":
                article["authors"] = [] if val == "—" else [a.strip() for a in val.split(",") if a.strip()]
            elif key == "페이지":
                article["pages"] = "" if val == "—" else val
            elif key == "DOI":
                dm = _RE_DOI_LINK.search(val)
                article["doi"] = dm.group(1) if dm else ("" if val == "—" else val)
            elif key == "원문 링크":
                article["url"] = "" if val == "—" else val
            continue
        if in_abstract and line.strip() and not line.strip().startswith("_(초록"):
            abstract_lines.append(line.strip())
    _flush()

    return issue if issue["issue_label"] else None


def _normalise(issue: dict, key: str) -> dict:
    """Fill defaults so JSON-sourced issues match the shape the page expects."""
    issue.setdefault("journal_key", key)
    issue.setdefault("source_url", "")
    issue.setdefault("collected", "")
    for art in issue.get("articles", []):
        art.setdefault("authors", [])
        art.setdefault("pages", "")
        art.setdefault("doi", "")
        art.setdefault("url", "")
        art.setdefault("abstract", "")
    return issue


def load_issues(output_dir: Path, logger: logging.Logger | None = None) -> list[dict]:
    """Load every collected issue under ``output_dir``.

    Prefers the structured ``<key>_<id>.json`` sidecar (lossless); falls back to
    parsing the ``<key>_<id>.md`` digest when no JSON exists.
    """
    issues: list[dict] = []
    seen: set[str] = set()

    for path in sorted(output_dir.glob("*.json")):
        key = path.stem.split("_", 1)[0]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            if logger:
                logger.warning("build_site: could not read %s", path)
            continue
        issues.append(_normalise(data, key))
        seen.add(path.stem)

    for path in sorted(output_dir.glob("*.md")):
        if path.stem in seen:
            continue  # already loaded from JSON
        key = path.stem.split("_", 1)[0]
        parsed = _parse_markdown(path.read_text(encoding="utf-8"), key)
        if parsed:
            issues.append(parsed)
        elif logger:
            logger.warning("build_site: could not parse %s", path)

    # Sort by publication date (newest first), interleaving the two journals,
    # rather than grouping by journal.
    return sorted(issues, key=_pub_sort_key, reverse=True)


# --- Publication date (for chronological ordering) --------------------------
#
# The collected JSON has no explicit publication date, but it can be derived
# from data we do store, and the result is verified against real anchors:
#   • Year: taken from the DOI (e.g. 10.20923/kjas.2026.88.005 -> 2026), which
#     both journals embed reliably.
#   • Month: both journals publish quarterly, confirmed by live publication
#     dates — jksarm v26/1 = 2026-02-28 (No.1 = Feb) and kjas n88 = 2026-04-30.
# This yields an accurate (year, quarter-month) ordering without re-scraping.

_RE_DOI_YEAR = re.compile(r"\.((?:19|20)\d\d)\.")
_JKSARM_MONTH = {1: 2, 2: 5, 3: 8, 4: 11}   # 한국기록관리학회지: Feb/May/Aug/Nov
_KJAS_MONTHS = [1, 4, 7, 10]                 # 기록학연구: Jan/Apr/Jul/Oct (quarterly)


def _pub_year(issue: dict) -> int:
    for art in issue.get("articles", []):
        m = _RE_DOI_YEAR.search(art.get("doi", ""))
        if m:
            return int(m.group(1))
    m = re.search(r"/((?:19|20)\d\d)\b", issue.get("source_url", ""))
    return int(m.group(1)) if m else 0


def _pub_month(issue: dict) -> int:
    key = issue.get("journal_key", "")
    if key == "jksarm":
        parts = issue.get("issue_id", "").split("-")
        no = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        return _JKSARM_MONTH.get(no, 6)
    if key == "kjas":
        sid = issue.get("issue_id", "")
        serial = int(sid) if sid.isdigit() else 0
        # Anchor: serial 88 -> April. Each serial step is one quarter.
        return _KJAS_MONTHS[(serial - 87) % 4]
    return 6


def _pub_sort_key(issue: dict) -> tuple:
    # (year, month, then issue recency as a tiebreak) — all ascending; the
    # caller sorts reverse=True for newest first.
    nums = tuple(int(n) for n in re.findall(r"\d+", issue.get("issue_id", "")))
    return (_pub_year(issue), _pub_month(issue), nums)


# --- HTML rendering ---------------------------------------------------------
#
# The article list is rendered to real HTML at build time, so the page shows
# all content even when JavaScript is disabled or stripped (e.g. file previews).
# The inline script is pure progressive enhancement: it only filters/searches
# the already-present DOM, never builds it.

_CSS = """
  :root {
    --bg: #f6f7f9; --card: #ffffff; --ink: #1f2328; --muted: #6b7280;
    --line: #e5e7eb; --accent: #2f6f4f; --accent-soft: #e7f1ec; --chip: #eef2f7;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--ink);
    font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo",
      "Malgun Gothic", "Noto Sans KR", Segoe UI, Roboto, sans-serif;
    line-height: 1.6;
  }
  header { background: var(--accent); color: #fff; padding: 28px 20px; }
  .wrap { max-width: 920px; margin: 0 auto; padding: 0 20px; }
  header h1 { margin: 0 0 6px; font-size: 1.5rem; }
  header p { margin: 0; opacity: .9; font-size: .9rem; }
  .controls {
    position: sticky; top: 0; z-index: 5; background: var(--bg);
    border-bottom: 1px solid var(--line); padding: 14px 0;
  }
  .controls .wrap { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
  #search {
    flex: 1 1 240px; min-width: 200px; padding: 10px 12px; font-size: .95rem;
    border: 1px solid var(--line); border-radius: 8px; background: #fff;
  }
  #search-btn {
    padding: 10px 18px; font-size: .95rem; border: none; border-radius: 8px;
    background: var(--accent); color: #fff; cursor: pointer; white-space: nowrap;
  }
  #search-btn:hover { filter: brightness(1.08); }
  #search-clear {
    padding: 10px 14px; font-size: .9rem; border: 1px solid var(--line);
    border-radius: 8px; background: #fff; color: var(--muted); cursor: pointer;
    white-space: nowrap;
  }
  .filters { display: flex; gap: 6px; flex-wrap: wrap; }
  .filter {
    border: 1px solid var(--line); background: #fff; color: var(--ink);
    padding: 8px 14px; border-radius: 999px; cursor: pointer; font-size: .9rem;
  }
  .filter.active { background: var(--accent); color: #fff; border-color: var(--accent); }
  #count { color: var(--muted); font-size: .85rem; margin-left: auto; }
  main { padding: 24px 0 60px; }
  .issue { margin-bottom: 34px; }
  .issue h2 {
    font-size: 1.15rem; margin: 0 0 4px; padding-bottom: 8px;
    border-bottom: 2px solid var(--accent-soft);
  }
  .issue .meta { color: var(--muted); font-size: .82rem; margin: 0 0 14px; }
  .issue .meta a { color: var(--accent); }
  .card {
    background: var(--card); border: 1px solid var(--line); border-radius: 12px;
    padding: 16px 18px; margin-bottom: 12px;
  }
  .card h3 { margin: 0 0 8px; font-size: 1.02rem; }
  .tags { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }
  .tag {
    background: var(--chip); color: var(--muted); border-radius: 6px;
    padding: 2px 8px; font-size: .78rem;
  }
  .tag a { color: var(--accent); text-decoration: none; }
  .abstract { font-size: .92rem; color: #374151; margin: 0; }
  .abstract.clamp {
    display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .toggle {
    margin-top: 8px; background: none; border: none; color: var(--accent);
    cursor: pointer; font-size: .82rem; padding: 0;
  }
  .empty { text-align: center; color: var(--muted); padding: 60px 0; }
  footer { color: var(--muted); font-size: .8rem; text-align: center; padding: 24px; }
"""

# Progressive enhancement only: filters/searches the pre-rendered DOM.
_JS = """
(function () {
  var searchEl = document.getElementById("search");
  var countEl = document.getElementById("count");
  var emptyEl = document.getElementById("empty");
  var sections = [].slice.call(document.querySelectorAll(".issue"));
  var filters = [].slice.call(document.querySelectorAll(".filter"));
  var activeJournal = "all";
  var query = "";

  // Clamp long abstracts and add a 더 보기/접기 toggle (only when JS runs).
  [].slice.call(document.querySelectorAll(".abstract")).forEach(function (p) {
    if ((p.textContent || "").length > 220) {
      p.classList.add("clamp");
      var btn = document.createElement("button");
      btn.className = "toggle";
      btn.type = "button";
      btn.textContent = "더 보기";
      btn.addEventListener("click", function () {
        p.classList.toggle("clamp");
        btn.textContent = p.classList.contains("clamp") ? "더 보기" : "접기";
      });
      p.parentNode.appendChild(btn);
    }
  });

  function apply() {
    var shown = 0;
    sections.forEach(function (sec) {
      if (activeJournal !== "all" && sec.getAttribute("data-journal") !== activeJournal) {
        sec.style.display = "none";
        return;
      }
      var cards = sec.querySelectorAll(".card");
      var visible = 0;
      for (var i = 0; i < cards.length; i++) {
        var ok = !query || (cards[i].getAttribute("data-text") || "").indexOf(query) !== -1;
        cards[i].style.display = ok ? "" : "none";
        if (ok) visible++;
      }
      sec.style.display = visible ? "" : "none";
      shown += visible;
    });
    countEl.textContent = shown ? ("논문 " + shown + "편") : "";
    if (emptyEl) emptyEl.style.display = shown ? "none" : "";
  }

  function runSearch() { query = searchEl.value.trim().toLowerCase(); apply(); }
  searchEl.addEventListener("input", runSearch);
  searchEl.addEventListener("keydown", function (e) { if (e.key === "Enter") runSearch(); });
  document.getElementById("search-btn").addEventListener("click", runSearch);
  document.getElementById("search-clear").addEventListener("click", function () {
    searchEl.value = ""; runSearch(); searchEl.focus();
  });
  filters.forEach(function (b) {
    b.addEventListener("click", function () {
      activeJournal = b.getAttribute("data-journal");
      filters.forEach(function (x) { x.classList.remove("active"); });
      b.classList.add("active");
      apply();
    });
  });

  apply();
})();
"""


def _esc(s: str) -> str:
    """Escape text for HTML body content."""
    return html.escape(s or "")


def _attr(s: str) -> str:
    """Escape text for an HTML attribute value (quotes included)."""
    return html.escape(s or "", quote=True)


def _render_card(art: dict) -> str:
    title = art.get("title", "")
    authors = art.get("authors") or []
    abstract = art.get("abstract", "")
    # Lowercased haystack for client-side search (title + authors + abstract).
    haystack = " ".join([title, " ".join(authors), abstract]).lower()

    tags: list[str] = []
    if authors:
        tags.append(f'<span class="tag">{_esc(", ".join(authors))}</span>')
    if art.get("pages"):
        tags.append(f'<span class="tag">pp. {_esc(art["pages"])}</span>')
    if art.get("doi"):
        tags.append(
            f'<span class="tag"><a href="https://doi.org/{_attr(art["doi"])}" '
            f'target="_blank" rel="noopener">DOI: {_esc(art["doi"])}</a></span>'
        )
    if art.get("url"):
        tags.append(
            f'<span class="tag"><a href="{_attr(art["url"])}" '
            f'target="_blank" rel="noopener">원문</a></span>'
        )

    abstract_html = _esc(abstract) if abstract else "<em>(초록 없음)</em>"
    return (
        f'<article class="card" data-text="{_attr(haystack)}">'
        f"<h3>{_esc(title)}</h3>"
        f'<div class="tags">{"".join(tags)}</div>'
        f'<p class="abstract">{abstract_html}</p>'
        f"</article>"
    )


def _render_issue(issue: dict) -> str:
    cards = "".join(_render_card(a) for a in issue.get("articles", []))
    src = (
        f' · <a href="{_attr(issue["source_url"])}" target="_blank" rel="noopener">출처</a>'
        if issue.get("source_url") else ""
    )
    year = _pub_year(issue)
    year_txt = f"{year}년 · " if year else ""
    return (
        f'<section class="issue" data-journal="{_attr(issue.get("journal_key", ""))}">'
        f'<h2>{_esc(issue.get("journal_name", ""))} — {_esc(issue.get("issue_label", ""))}</h2>'
        f'<p class="meta">{year_txt}논문 {len(issue.get("articles", []))}편 · '
        f'수집일 {_esc(issue.get("collected", ""))}{src}</p>'
        f"{cards}</section>"
    )


def _render_filters(issues: list[dict]) -> str:
    seen: dict[str, str] = {}
    for iss in issues:
        key = iss.get("journal_key", "")
        seen.setdefault(key, iss.get("journal_name", key))
    buttons = ['<button class="filter active" type="button" data-journal="all">전체</button>']
    for key, name in seen.items():
        buttons.append(
            f'<button class="filter" type="button" data-journal="{_attr(key)}">{_esc(name)}</button>'
        )
    return "".join(buttons)


def render_site(issues: list[dict]) -> str:
    """Render the full single-page HTML for the given issues (content pre-rendered)."""
    total = sum(len(i.get("articles", [])) for i in issues)
    main_html = "".join(_render_issue(i) for i in issues)
    if not main_html:
        main_html = '<p class="empty">아직 수집된 논문이 없습니다.</p>'

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>기록학 학회지 아카이브</title>
<style>{_CSS}</style>
</head>
<body>
<header>
  <div class="wrap">
    <h1>기록학 학회지 아카이브</h1>
    <p>한국기록관리학회지 · 기록학연구 — 새 호가 나오면 자동 수집한 목차·초록 모음 (AI 요약 없음)</p>
  </div>
</header>

<div class="controls">
  <div class="wrap">
    <input id="search" type="search" placeholder="제목·저자·초록 검색…" autocomplete="off">
    <button id="search-btn" type="button">검색</button>
    <button id="search-clear" type="button">초기화</button>
    <div class="filters">{_render_filters(issues)}</div>
    <span id="count">논문 {total}편</span>
  </div>
</div>

<main class="wrap">
{main_html}
<p class="empty" id="empty" style="display:none">조건에 맞는 논문이 없습니다.</p>
</main>

<footer>마지막 생성: {date.today().isoformat()} · 정적 페이지 (서버 불필요)</footer>

<script>{_JS}</script>
</body>
</html>
"""


def build_site(
    output_dir: str | Path = OUTPUT_DIR,
    site_dir: str | Path = SITE_DIR,
    logger: logging.Logger | None = None,
) -> Path:
    """Build ``site/index.html`` from the digests in ``output_dir``."""
    output_dir, site_dir = Path(output_dir), Path(site_dir)
    issues = load_issues(output_dir, logger)
    site_dir.mkdir(parents=True, exist_ok=True)
    index = site_dir / "index.html"
    index.write_text(render_site(issues), encoding="utf-8")
    n_articles = sum(len(i["articles"]) for i in issues)
    if logger:
        logger.info("build_site: wrote %s (%d issues, %d articles)", index, len(issues), n_articles)
    return index


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build the static site from output/ digests.")
    parser.add_argument("--output", default=str(OUTPUT_DIR), help="directory of *.md digests")
    parser.add_argument("--site", default=str(SITE_DIR), help="directory to write index.html into")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logger = logging.getLogger("build_site")
    path = build_site(args.output, args.site, logger)
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
