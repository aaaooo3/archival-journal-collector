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
import os
import re
import time

import requests
from bs4 import BeautifulSoup

from build_site import build_site, load_issues
from sources.base import ExtractionError, fetch_html, make_session
from tooling import OUTPUT_DIR, SITE_DIR, SUMMARY_DIR, setup_logging

DEFAULT_SENTENCES = 5   # max key sentences per chapter (extractive engine)
DEFAULT_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_OLLAMA_MODEL = "qwen2.5:7b"   # strong Korean; override with --model

# Generative (Ollama) engine — one chapter at a time.
# 일부 모델(qwen 등)이 기술적 내용에서 중국어/영어로 빠지는 경향이 있어, 한국어
# 사용을 시스템 메시지와 프롬프트 양쪽에서 강하게 지시한다.
_OLLAMA_SYSTEM = (
    "당신은 한국어 학술 요약 도우미입니다. 어떤 경우에도 반드시 한국어(한글)로만 "
    "답하세요. 영어·중국어 등 다른 언어로는 절대 쓰지 마세요."
)
_OLLAMA_PROMPT = """다음은 한국어 학술 논문의 한 장(章)입니다. 이 장의 핵심 내용을 \
**한국어**로 3~4문장 충실하게 요약하세요.

규칙:
- 반드시 한국어(한글)로만 작성합니다. 영어·중국어 등 다른 언어 사용 금지.
- 아래 제공된 내용에만 근거하고, 없는 내용을 지어내지 않습니다.
- 머리말이나 목록 기호 없이 요약문만 출력합니다.

[장 제목] {heading}
[내용]
{body}
"""


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


# --- Generative engine (local Ollama, free) ---------------------------------


def ollama_available(host: str, logger: logging.Logger) -> bool:
    """True if an Ollama server answers at ``host``."""
    try:
        return requests.get(f"{host.rstrip('/')}/api/tags", timeout=5).ok
    except requests.RequestException:
        return False


def ollama_generate(host: str, model: str, prompt: str, system: str | None = None,
                    timeout: int = 240, num_predict: int = 400) -> str:
    """Call Ollama's /api/generate and return the model's text response."""
    payload = {"model": model, "prompt": prompt, "stream": False,
               "options": {"temperature": 0.2, "num_predict": num_predict}}
    if system:
        payload["system"] = system
    resp = requests.post(f"{host.rstrip('/')}/api/generate", json=payload, timeout=timeout)
    resp.raise_for_status()
    return (resp.json().get("response") or "").strip()


def summarize_sections_ollama(text: str, host: str, model: str, logger: logging.Logger) -> list[dict] | None:
    """Generative chapter-by-chapter summary via a local Ollama model.

    Returns [{"heading", "summary"}], or None if no chapters were detected or a
    generation call failed (caller then falls back to the extractive engine).
    """
    from summarizer import section_bodies

    bodies = section_bodies(text)
    if not bodies:
        return None

    sections: list[dict] = []
    for heading, body in bodies:
        prompt = _OLLAMA_PROMPT.format(heading=heading, body=body[:6000])
        try:
            out = ollama_generate(host, model, prompt, system=_OLLAMA_SYSTEM)
        except requests.RequestException as exc:
            logger.error("  Ollama 생성 실패 (%s): %s", heading, exc)
            return None
        if out:
            sections.append({"heading": heading, "summary": out})
            logger.info("   · %s → %d자", heading, len(out))
    return sections or None


def summarize_row(session, row: dict, opts: argparse.Namespace, logger: logging.Logger) -> dict | None:
    """Download, extract and summarise one article. Returns a summary dict or None."""
    from summarizer import summarize, summarize_by_section

    per_section = opts.sentences
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

    # Generative engine (local Ollama) — falls through to extractive on failure.
    if opts.engine == "ollama":
        gen = summarize_sections_ollama(text, opts.ollama_host, opts.model, logger)
        if gen:
            logger.info("  → %d개 장 생성 요약 (모델 %s, 원문 %d자)", len(gen), opts.model, len(text))
            result["method"] = f"ollama-{opts.model}"
            result["sections"] = gen
            return result
        logger.warning("  Ollama 요약 실패/장 미검출 — 추출 요약으로 대체합니다")

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


def _list_number(value: str) -> int:
    """Parse a --list number, tolerating a leading '#' (e.g. '#7')."""
    return int(value.lstrip("#").strip())


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("selectors", nargs="*", help="article DOIs, or --list numbers")
    p.add_argument("--num", type=_list_number, nargs="*", default=[], help="select by --list numbers (e.g. 2 5)")
    p.add_argument("--list", action="store_true", help="print a numbered list of all articles and exit")
    p.add_argument("--engine", choices=["extractive", "ollama"], default="extractive",
                   help="요약 방식: extractive(무료·로컬 추출, 기본) 또는 ollama(무료·로컬 생성형)")
    p.add_argument("--model", default=DEFAULT_OLLAMA_MODEL,
                   help=f"Ollama 모델명 (default {DEFAULT_OLLAMA_MODEL})")
    p.add_argument("--ollama-host", default=DEFAULT_OLLAMA_HOST,
                   help=f"Ollama 서버 주소 (default {DEFAULT_OLLAMA_HOST})")
    p.add_argument("--sentences", type=int, default=DEFAULT_SENTENCES,
                   help=f"[extractive] 장별 최대 문장 수 (default {DEFAULT_SENTENCES}; 장이 길수록 이 한도까지 늘어남)")
    p.add_argument("--force", action="store_true", help="re-summarise even if a summary already exists")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    logger = setup_logging("summarize")
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
    for sel in args.selectors:
        token = sel.lstrip("#").strip()
        if token.isdigit():                       # a bare list number works too
            n = int(token)
            if 1 <= n <= len(rows):
                selected.append(rows[n - 1])
            else:
                logger.warning("번호 %d 는 범위를 벗어남 (1..%d)", n, len(rows))
        elif sel in by_doi:
            selected.append(by_doi[sel])
        else:
            logger.warning("목록에서 찾지 못함(번호 또는 DOI): %s", sel)

    if not selected:
        logger.error("선택된 논문이 없습니다. --list 로 번호를 확인하세요.")
        return 1

    if args.engine == "ollama" and not ollama_available(args.ollama_host, logger):
        logger.error(
            "Ollama 서버에 연결할 수 없습니다 (%s).\n"
            "  1) https://ollama.com 에서 설치 후  `ollama serve` 실행\n"
            "  2) 모델 받기:  ollama pull %s\n"
            "  3) 다시 실행:  python summarize.py --engine ollama --num ...\n"
            "  (또는 --engine extractive 로 무료 추출 요약 사용)",
            args.ollama_host, args.model,
        )
        return 2

    session = make_session()
    done = 0
    for row in selected:
        out_path = SUMMARY_DIR / f"{_safe_name(row['doi'], row)}.json"
        if out_path.exists() and not args.force:
            logger.info("이미 요약됨, 건너뜀: %s (--force 로 재생성)", row["title"][:40])
            continue
        summary = summarize_row(session, row, args, logger)
        if summary:
            save_summary(summary, row, logger)
            done += 1

    logger.info("요약 완료: %d편", done)
    if done:
        build_site(OUTPUT_DIR, SITE_DIR, logger)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
