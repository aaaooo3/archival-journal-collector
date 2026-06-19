#!/usr/bin/env python3
"""수집된 논문 중 초록이 비어 있는 것을, 논문 페이지에서 긁어와 보강(best-effort).

Crossref에 초록이 없던 해외 논문에 대해 각 논문의 공개 페이지(DOI 링크가 향하는
출판사/저널 페이지)에서 표준 메타태그(``citation_abstract`` 등)나 본문 초록 영역을
찾아 채웁니다.

- Archivaria(OJS) 등 공개 사이트는 잘 채워집니다.
- JSTOR·Taylor & Francis 등 봇 차단이 강한 곳은 막혀서 건너뜁니다(정상, 빠르게 skip).
- 이미 초록이 있는 논문(국내 등)은 건드리지 않습니다.

사용:
    python enrich_abstracts.py                  # 초록 없는 모든 논문 시도
    python enrich_abstracts.py --journal archivaria
    python enrich_abstracts.py --force          # 이미 채워진 것도 다시 시도
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from build_site import build_site
from sources.base import DEFAULT_HEADERS, clean_text

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"
SITE_DIR = ROOT / "site"
LOG_DIR = ROOT / "logs"

REQUEST_TIMEOUT = 15          # 빠르게 실패하도록 짧게 (차단 사이트에서 오래 안 매달림)
REQUEST_DELAY_SECONDS = 1.5

# 본문 초록 영역 후보 (OJS 등). 메타태그가 우선.
_ABSTRACT_SELECTORS = [
    "section.item.abstract", "div.item.abstract", ".article-abstract",
    "#abstract", ".abstract", "#articleAbstract",
]
_META_ABSTRACT = [("citation_abstract", "name"), ("dc.Description", "name"),
                  ("dc.description", "name"), ("description", "name"),
                  ("og:description", "property")]


def setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("enrich")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(h)
    return logger


def _fetch(session: requests.Session, url: str, logger: logging.Logger) -> str | None:
    """한 번만 시도하고 실패하면 None (차단 사이트에서 빨리 건너뛰기 위해)."""
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        resp.raise_for_status()
        if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
            resp.encoding = resp.apparent_encoding
        return resp.text
    except requests.RequestException as exc:
        logger.info("    건너뜀 (페이지 접근 불가: %s)", type(exc).__name__)
        return None


def extract_abstract(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    # 1) 표준 메타태그 (citation_abstract 등) — 가장 신뢰.
    for name, attr in _META_ABSTRACT:
        el = soup.find("meta", attrs={attr: name})
        if el and el.get("content"):
            text = clean_text(el["content"])
            if len(text) >= 80:
                return text
    # 2) 본문 초록 영역 (OJS 등).
    for sel in _ABSTRACT_SELECTORS:
        node = soup.select_one(sel)
        if node:
            text = clean_text(node.get_text(" "))
            text = re.sub(r"^\s*(abstract|초록)\s*[:\-]?\s*", "", text, flags=re.IGNORECASE)
            if len(text) >= 80:
                return text
    return ""


def _landing_url(art: dict) -> str:
    url = art.get("url", "")
    if url:
        return url
    doi = art.get("doi", "")
    return f"https://doi.org/{doi}" if doi else ""


def enrich_file(path: Path, session, force: bool, logger: logging.Logger) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    filled = 0
    for art in data.get("articles", []):
        if art.get("abstract") and not force:
            continue
        url = _landing_url(art)
        if not url:
            continue
        logger.info("  · %s", art.get("title", "")[:50])
        html = _fetch(session, url, logger)
        time.sleep(REQUEST_DELAY_SECONDS)
        if not html:
            continue
        abstract = extract_abstract(html)
        if abstract:
            art["abstract"] = abstract
            filled += 1
            logger.info("    → 초록 채움 (%d자)", len(abstract))
    if filled:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return filled


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--journal", default="all", help="특정 학회지 키만 (예: archivaria). 기본 all")
    p.add_argument("--force", action="store_true", help="이미 초록 있는 것도 다시 시도")
    args = p.parse_args(argv)
    logger = setup_logging()

    files = sorted(f for f in OUTPUT_DIR.glob("*.json"))
    if args.journal != "all":
        files = [f for f in files if f.stem.startswith(f"{args.journal}_")]
    if not files:
        logger.error("대상 파일이 없습니다 (output/*.json).")
        return 1

    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)

    total = 0
    for path in files:
        logger.info("[%s]", path.stem)
        total += enrich_file(path, session, args.force, logger)

    logger.info("초록 보강 완료: %d편 채움", total)
    if total:
        build_site(OUTPUT_DIR, SITE_DIR, logger)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
