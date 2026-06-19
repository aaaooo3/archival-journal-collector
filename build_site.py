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

    def _sort_key(iss: dict) -> tuple:
        # Group by journal order, then newest issue first.
        try:
            ji = JOURNAL_ORDER.index(iss["journal_key"])
        except ValueError:
            ji = len(JOURNAL_ORDER)
        nums = tuple(int(n) for n in re.findall(r"\d+", iss["issue_id"]))
        return (ji, tuple(-n for n in nums))

    return sorted(issues, key=_sort_key)


# --- HTML rendering ---------------------------------------------------------

_PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>기록학 학회지 아카이브</title>
<style>
  :root {{
    --bg: #f6f7f9; --card: #ffffff; --ink: #1f2328; --muted: #6b7280;
    --line: #e5e7eb; --accent: #2f6f4f; --accent-soft: #e7f1ec; --chip: #eef2f7;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; background: var(--bg); color: var(--ink);
    font-family: -apple-system, BlinkMacSystemFont, "Apple SD Gothic Neo",
      "Malgun Gothic", "Noto Sans KR", Segoe UI, Roboto, sans-serif;
    line-height: 1.6;
  }}
  header {{
    background: var(--accent); color: #fff; padding: 28px 20px;
  }}
  .wrap {{ max-width: 920px; margin: 0 auto; padding: 0 20px; }}
  header h1 {{ margin: 0 0 6px; font-size: 1.5rem; }}
  header p {{ margin: 0; opacity: .9; font-size: .9rem; }}
  .controls {{
    position: sticky; top: 0; z-index: 5; background: var(--bg);
    border-bottom: 1px solid var(--line); padding: 14px 0;
  }}
  .controls .wrap {{ display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }}
  #search {{
    flex: 1 1 240px; min-width: 200px; padding: 10px 12px; font-size: .95rem;
    border: 1px solid var(--line); border-radius: 8px; background: #fff;
  }}
  #search-btn {{
    padding: 10px 18px; font-size: .95rem; border: none; border-radius: 8px;
    background: var(--accent); color: #fff; cursor: pointer; white-space: nowrap;
  }}
  #search-btn:hover {{ filter: brightness(1.08); }}
  #search-clear {{
    padding: 10px 14px; font-size: .9rem; border: 1px solid var(--line);
    border-radius: 8px; background: #fff; color: var(--muted); cursor: pointer;
    white-space: nowrap;
  }}
  .filters {{ display: flex; gap: 6px; flex-wrap: wrap; }}
  .filter {{
    border: 1px solid var(--line); background: #fff; color: var(--ink);
    padding: 8px 14px; border-radius: 999px; cursor: pointer; font-size: .9rem;
  }}
  .filter.active {{ background: var(--accent); color: #fff; border-color: var(--accent); }}
  #count {{ color: var(--muted); font-size: .85rem; margin-left: auto; }}
  main {{ padding: 24px 0 60px; }}
  .issue {{ margin-bottom: 34px; }}
  .issue h2 {{
    font-size: 1.15rem; margin: 0 0 4px; padding-bottom: 8px;
    border-bottom: 2px solid var(--accent-soft);
  }}
  .issue .meta {{ color: var(--muted); font-size: .82rem; margin: 0 0 14px; }}
  .issue .meta a {{ color: var(--accent); }}
  .card {{
    background: var(--card); border: 1px solid var(--line); border-radius: 12px;
    padding: 16px 18px; margin-bottom: 12px;
  }}
  .card h3 {{ margin: 0 0 8px; font-size: 1.02rem; }}
  .tags {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 10px; }}
  .tag {{
    background: var(--chip); color: var(--muted); border-radius: 6px;
    padding: 2px 8px; font-size: .78rem;
  }}
  .tag a {{ color: var(--accent); text-decoration: none; }}
  .abstract {{ font-size: .92rem; color: #374151; margin: 0; }}
  .abstract.clamp {{
    display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical;
    overflow: hidden;
  }}
  .toggle {{
    margin-top: 8px; background: none; border: none; color: var(--accent);
    cursor: pointer; font-size: .82rem; padding: 0;
  }}
  .empty {{ text-align: center; color: var(--muted); padding: 60px 0; }}
  mark {{ background: #fff3b0; padding: 0 1px; }}
  footer {{ color: var(--muted); font-size: .8rem; text-align: center; padding: 24px; }}
</style>
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
    <div class="filters" id="filters"></div>
    <span id="count"></span>
  </div>
</div>

<main class="wrap" id="app"></main>

<footer>마지막 생성: {generated} · 정적 페이지 (서버 불필요)</footer>

<script id="data" type="application/json">{data_json}</script>
<script>
  const ISSUES = JSON.parse(document.getElementById("data").textContent);
  const JOURNALS = [{{key:"all", name:"전체"}}].concat(
    [...new Set(ISSUES.map(i => i.journal_key))].map(k => ({{
      key: k, name: (ISSUES.find(i => i.journal_key === k) || {{}}).journal_name || k
    }}))
  );
  let activeJournal = "all";
  let query = "";

  const filtersEl = document.getElementById("filters");
  JOURNALS.forEach(j => {{
    const b = document.createElement("button");
    b.className = "filter" + (j.key === "all" ? " active" : "");
    b.textContent = j.name;
    b.onclick = () => {{
      activeJournal = j.key;
      [...filtersEl.children].forEach(c => c.classList.remove("active"));
      b.classList.add("active");
      render();
    }};
    filtersEl.appendChild(b);
  }});

  const searchEl = document.getElementById("search");
  function runSearch() {{ query = searchEl.value.trim().toLowerCase(); render(); }}
  searchEl.addEventListener("input", runSearch);          // 입력하는 대로 실시간 검색
  searchEl.addEventListener("keydown", e => {{ if (e.key === "Enter") runSearch(); }});
  document.getElementById("search-btn").addEventListener("click", runSearch);
  document.getElementById("search-clear").addEventListener("click", () => {{
    searchEl.value = ""; runSearch(); searchEl.focus();
  }});

  function esc(s) {{
    return (s || "").replace(/[&<>"]/g, c => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}}[c]));
  }}
  function highlight(s) {{
    s = esc(s);
    if (!query) return s;
    const q = query.replace(/[.*+?^${{}}()|[\\]\\\\]/g, "\\\\$&");
    return s.replace(new RegExp(q, "gi"), m => "<mark>" + m + "</mark>");
  }}
  function matches(art) {{
    if (!query) return true;
    const hay = (art.title + " " + (art.authors || []).join(" ") + " " + art.abstract).toLowerCase();
    return hay.includes(query);
  }}

  function render() {{
    const app = document.getElementById("app");
    app.innerHTML = "";
    let shown = 0;

    ISSUES.filter(i => activeJournal === "all" || i.journal_key === activeJournal).forEach(issue => {{
      const arts = issue.articles.filter(matches);
      if (!arts.length) return;
      shown += arts.length;

      const sec = document.createElement("section");
      sec.className = "issue";
      const src = issue.source_url
        ? ` · <a href="${{esc(issue.source_url)}}" target="_blank" rel="noopener">출처</a>` : "";
      sec.innerHTML =
        `<h2>${{esc(issue.journal_name)}} — ${{esc(issue.issue_label)}}</h2>` +
        `<p class="meta">논문 ${{arts.length}}편 · 수집일 ${{esc(issue.collected)}}${{src}}</p>`;

      arts.forEach(art => {{
        const card = document.createElement("div");
        card.className = "card";
        const tags = [];
        if ((art.authors || []).length) tags.push(`<span class="tag">${{highlight(art.authors.join(", "))}}</span>`);
        if (art.pages) tags.push(`<span class="tag">pp. ${{esc(art.pages)}}</span>`);
        if (art.doi) tags.push(`<span class="tag"><a href="https://doi.org/${{esc(art.doi)}}" target="_blank" rel="noopener">DOI: ${{esc(art.doi)}}</a></span>`);
        if (art.url) tags.push(`<span class="tag"><a href="${{esc(art.url)}}" target="_blank" rel="noopener">원문</a></span>`);
        const longAbs = (art.abstract || "").length > 220;
        card.innerHTML =
          `<h3>${{highlight(art.title)}}</h3>` +
          `<div class="tags">${{tags.join("")}}</div>` +
          `<p class="abstract${{longAbs ? " clamp" : ""}}">${{highlight(art.abstract) || "<em>(초록 없음)</em>"}}</p>` +
          (longAbs ? `<button class="toggle">더 보기</button>` : "");
        if (longAbs) {{
          const p = card.querySelector(".abstract"), t = card.querySelector(".toggle");
          t.onclick = () => {{ p.classList.toggle("clamp"); t.textContent = p.classList.contains("clamp") ? "더 보기" : "접기"; }};
        }}
        sec.appendChild(card);
      }});
      app.appendChild(sec);
    }});

    document.getElementById("count").textContent = shown ? `논문 ${{shown}}편` : "";
    if (!shown) app.innerHTML = `<p class="empty">조건에 맞는 논문이 없습니다.</p>`;
  }}

  render();
</script>
</body>
</html>
"""


def render_site(issues: list[dict]) -> str:
    """Render the full single-page HTML for the given issues."""
    data_json = json.dumps(issues, ensure_ascii=False)
    # Keep the embedded JSON from prematurely closing the <script> tag.
    data_json = data_json.replace("</", "<\\/")
    return _PAGE_TEMPLATE.format(
        generated=date.today().isoformat(),
        data_json=data_json,
    )


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
