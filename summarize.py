#!/usr/bin/env python3
"""Summarise selected articles from the collected list — free and local.

Pick articles (by DOI or by the number shown in ``--list``), download each
article's PDF, extract its text, and produce an *extractive* summary (key
sentences copied verbatim — no paid API, no model). Summaries are saved under
``output/summaries/`` and shown on the static site after a rebuild.

Usage
-----
    python summarize.py --list                       # number every article
    python summarize.py --num 2 5                    # summarise list items 2 and 5
    python summarize.py 10.20923/kjas.2026.88.005    # summarise by DOI
    python summarize.py --num 3 --sentences 8        # longer summary
    python summarize.py --num 3 --force              # re-summarise (overwrite)

PDFs are fetched politely (User-Agent + delay + retries). Articles without a
reachable PDF are skipped with a clear message.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import re
import sys
import time
from pathlib import Path

from bs4 import BeautifulSoup

from build_site import build_site, load_issues
from sources.base import ExtractionError, fetch_html, make_session

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"
SUMMARY_DIR = OUTPUT_DIR / "summaries"
SITE_DIR = ROOT / "site"
LOG_DIR = ROOT / "logs"

DEFAULT_SENTENCES = 5   # max key sentences per chapter


def setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("summarize")
    logger.setLevel(logging.DEBUG)
    if logger.handlers:
        return logger
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh = logging.FileHandler(LOG_DIR / "watcher.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger


def _flatten(issues: list[dict]) -> list[dict]:
    """Flatten issues into a numbered list of article rows (site display order)."""
    rows: list[dict] = []
    for iss in issues:
        for art in iss.get("articles", []):
            rows.append({
                "journal_key": iss.get("journal_key", ""),
                "journal_name": iss.get("journal_name", ""),
                "issue_id": iss.get("issue_id", ""),
                "issue_label": iss.get("issue_label", ""),
                "title": art.get("title", ""),
                "authors": art.get("authors", []),
                "doi": art.get("doi", ""),
                "url": art.get("url", ""),
            })
    return rows


def _safe_name(doi: str, row: dict) -> str:
    base = doi or f'{row["journal_key"]}_{row["issue_id"]}_{row["title"][:20]}'
    return re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("_")


def resolve_pdf_url(session, row: dict, logger: logging.Logger) -> str:
    """Return a direct PDF URL for the article (via citation_pdf_url if needed)."""
    url = row.get("url", "")
    if url.lower().endswith(".pdf"):
        return url
    if not url:
        return ""
    html = fetch_html(session, url, logger)
    soup = BeautifulSoup(html, "lxml")
    el = soup.find("meta", attrs={"name": "citation_pdf_url"})
    if el and el.get("content"):
        return el["content"].strip()
    return ""


def download_pdf(session, url: str, logger: logging.Logger, retries: int = 4) -> bytes:
    """Download a PDF as bytes, with polite retry/backoff. Raises ExtractionError."""
    last = None
    for attempt in range(1, retries + 1):
        try:
            logger.debug("GET(pdf) %s (attempt %d/%d)", url, attempt, retries)
            resp = session.get(url, timeout=60)
            resp.raise_for_status()
            if resp.content[:5] != b"%PDF-":
                raise ExtractionError(f"not a PDF (content-type {resp.headers.get('content-type')})")
            time.sleep(2.5)
            return resp.content
        except Exception as exc:  # noqa: BLE001
            last = exc
            logger.warning("PDF fetch failed for %s (%s); retry in %ds", url, exc, 2 ** attempt)
            time.sleep(2 ** attempt)
    raise ExtractionError(f"could not download PDF {url}: {last}")


def extract_pdf_text(data: bytes) -> str:
    from pdfminer.high_level import extract_text  # imported lazily; heavy
    return extract_text(io.BytesIO(data)) or ""


def summarize_row(session, row: dict, per_section: int, logger: logging.Logger) -> dict | None:
    """Download, extract and summarise one article. Returns a summary dict or None."""
    from summarizer import summarize, summarize_by_section

    title = row["title"][:50]
    logger.info("summarising %r (%s)", title, row.get("doi") or "no-doi")
    try:
        pdf_url = resolve_pdf_url(session, row, logger)
    except ExtractionError as exc:
        logger.error("  could not resolve PDF url: %s", exc)
        return None
    if not pdf_url:
        logger.error("  no PDF url found — skipping")
        return None

    try:
        data = download_pdf(session, pdf_url, logger)
    except ExtractionError as exc:
        logger.error("  %s", exc)
        return None

    text = extract_pdf_text(data)
    if len(text.strip()) < 300:
        logger.error("  extracted text too short (%d chars) — skipping", len(text.strip()))
        return None

    result = {
        "doi": row.get("doi", ""),
        "title": row.get("title", ""),
        "authors": row.get("authors", []),
        "journal_key": row.get("journal_key", ""),
        "journal_name": row.get("journal_name", ""),
        "issue_id": row.get("issue_id", ""),
        "issue_label": row.get("issue_label", ""),
        "url": row.get("url", ""),
        "pdf_url": pdf_url,
        "source_chars": len(text),
    }

    sections = summarize_by_section(text, per_section=per_section)
    if sections:
        total = sum(len(s["sentences"]) for s in sections)
        logger.info("  → %d개 장, 총 %d문장 (원문 %d자)", len(sections), total, len(text))
        result["method"] = "extractive-textrank-sectioned"
        result["sections"] = sections
        return result

    # Fallback: no chapter structure detected -> flat summary.
    flat = summarize(text, max_sentences=max(6, per_section))
    if not flat:
        logger.error("  summariser produced nothing — skipping")
        return None
    logger.info("  → 장 구조 미검출, 핵심 %d문장 (원문 %d자)", len(flat), len(text))
    result["method"] = "extractive-textrank"
    result["summary"] = flat
    return result


def save_summary(summary: dict, row: dict, logger: logging.Logger) -> Path:
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    path = SUMMARY_DIR / f"{_safe_name(summary['doi'], row)}.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info("  saved %s", path)
    return path


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("selectors", nargs="*", help="article DOIs to summarise")
    p.add_argument("--num", type=int, nargs="*", default=[], help="select by --list numbers")
    p.add_argument("--list", action="store_true", help="print a numbered list of all articles and exit")
    p.add_argument("--sentences", type=int, default=DEFAULT_SENTENCES,
                   help=f"장별 최대 문장 수 (default {DEFAULT_SENTENCES}; 장이 길수록 이 한도까지 늘어남)")
    p.add_argument("--force", action="store_true", help="re-summarise even if a summary already exists")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logger = setup_logging()
    issues = load_issues(OUTPUT_DIR, logger)
    rows = _flatten(issues)

    if args.list or (not args.selectors and not args.num):
        print(f"\n수집된 논문 {len(rows)}편 (번호로 선택: python summarize.py --num N ...)\n")
        for i, r in enumerate(rows, 1):
            mark = "✓" if (SUMMARY_DIR / f"{_safe_name(r['doi'], r)}.json").exists() else " "
            authors = ", ".join(r["authors"][:2]) + ("…" if len(r["authors"]) > 2 else "")
            print(f"  [{mark}] {i:3}. ({r['journal_key']} {r['issue_label']}) {r['title'][:46]}  — {authors}")
        print("\n  ✓ = 이미 요약됨\n")
        return 0

    # Resolve selection -> rows.
    selected: list[dict] = []
    for n in args.num:
        if 1 <= n <= len(rows):
            selected.append(rows[n - 1])
        else:
            logger.warning("번호 %d 는 범위를 벗어남 (1..%d)", n, len(rows))
    by_doi = {r["doi"]: r for r in rows if r["doi"]}
    for doi in args.selectors:
        if doi in by_doi:
            selected.append(by_doi[doi])
        else:
            logger.warning("DOI를 목록에서 찾지 못함: %s", doi)

    if not selected:
        logger.error("선택된 논문이 없습니다. --list 로 번호를 확인하세요.")
        return 1

    session = make_session()
    done = 0
    for row in selected:
        out_path = SUMMARY_DIR / f"{_safe_name(row['doi'], row)}.json"
        if out_path.exists() and not args.force:
            logger.info("이미 요약됨, 건너뜀: %s (--force 로 재생성)", row["title"][:40])
            continue
        summary = summarize_row(session, row, args.sentences, logger)
        if summary:
            save_summary(summary, row, logger)
            done += 1

    logger.info("요약 완료: %d편", done)
    if done:
        build_site(OUTPUT_DIR, SITE_DIR, logger)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
