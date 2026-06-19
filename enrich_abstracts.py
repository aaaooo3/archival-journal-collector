#!/usr/bin/env python3
"""수집된 논문 중 초록이 비어 있는 것을 무료 학술 API/논문 페이지에서 보강.

각 논문에 대해 두 경로를 순서대로 시도합니다:
  1) OpenAlex API(무료·키 불필요)에서 DOI로 초록 조회 — 출판사 사이트가 봇을
     차단(JSTOR·Taylor & Francis 등)해도 우회 가능. 초록은 역색인으로 저장돼 있어
     원문장으로 복원합니다.
  2) (1)이 없으면 논문 공개 페이지에서 표준 메타태그(citation_abstract 등)/본문
     초록 영역을 긁기 — Archivaria 같은 OJS 공개 사이트용.

- 이미 초록이 있는 논문(국내 등)은 건드리지 않습니다(--force 로 강제 재시도).
- 어느 경로로도 못 찾으면 조용히 건너뜁니다(예: 초록 자체가 없는 리뷰글).

사용:
    python enrich_abstracts.py                  # 초록 없는 모든 논문 시도
    python enrich_abstracts.py --journal archrec
    python enrich_abstracts.py --force          # 이미 채워진 것도 다시 시도

OpenAlex 정중 사용을 위해 환경변수 OPENALEX_MAILTO 에 이메일을 넣으면 좋습니다(선택).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
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

OPENALEX = "https://api.openalex.org"
OPENALEX_MAILTO = os.environ.get("OPENALEX_MAILTO", "")

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


def _reconstruct_inverted(inv: dict | None) -> str:
    """OpenAlex stores abstracts as {word: [positions]}; rebuild the text."""
    if not inv:
        return ""
    positions: list[tuple[int, str]] = []
    for word, idxs in inv.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort()
    return clean_text(" ".join(w for _, w in positions))


def openalex_abstract(session: requests.Session, doi: str, logger: logging.Logger) -> str:
    """Fetch an abstract from OpenAlex by DOI (free, no key, not bot-blocked)."""
    if not doi:
        return ""
    url = (f"{OPENALEX}/works?filter=doi:{doi.lower()}"
           f"&per-page=1&select=abstract_inverted_index")
    if OPENALEX_MAILTO:
        url += f"&mailto={OPENALEX_MAILTO}"
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except requests.RequestException as exc:
        logger.info("    OpenAlex 조회 실패 (%s)", type(exc).__name__)
        return ""
    if not results:
        return ""
    return _reconstruct_inverted(results[0].get("abstract_inverted_index"))


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
        logger.info("  · %s", art.get("title", "")[:50])

        # 1) OpenAlex (DOI 조회) — 차단 사이트도 우회.
        abstract = openalex_abstract(session, art.get("doi", ""), logger)
        source = "OpenAlex"

        # 2) 없으면 논문 페이지 긁기 (OJS 공개 사이트용).
        if not abstract:
            url = _landing_url(art)
            if url:
                html = _fetch(session, url, logger)
                if html:
                    abstract = extract_abstract(html)
                    source = "페이지"
        time.sleep(REQUEST_DELAY_SECONDS)

        if abstract:
            art["abstract"] = abstract
            filled += 1
            logger.info("    → 초록 채움 (%s, %d자)", source, len(abstract))
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
