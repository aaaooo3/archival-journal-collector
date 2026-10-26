#!/usr/bin/env python3
"""논문의 '저자 키워드'를 가져와 주제어로 저장 — 무료, 대부분 논문에 있음.

LLM 추측(keywords.py)보다 정확한, **저자가 직접 단 키워드**를 쓴다. 각 논문의
공개 페이지에서 표준 메타태그(``citation_keywords``)·본문 "키워드/주제어" 섹션을
읽고, 해외 논문은 OpenAlex(무료 API)로 보강한다. 가져온 키워드는 ``keywords`` 와
``topics``/``topic`` 에 저장되어 사이트의 주제 태그·필터로 쓰인다.

사용:
    python3 fetch_keywords.py                 # 키워드 없는 모든 논문
    python3 fetch_keywords.py --journal kjas  # 한 학회지만
    python3 fetch_keywords.py --force         # 이미 있어도 다시 가져오기

멱등적(이미 키워드가 있으면 건너뜀). 네트워크가 필요하다(페이지·OpenAlex 조회).
LLM을 쓰지 않으므로 Ollama는 필요 없다. 못 찾은 논문은 keywords.py(로컬 LLM)로
메꾸면 된다.
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os

import requests

import build_site
from sources.base import ExtractionError, clean_keywords, fetch_html, keywords_from_html, make_session
from tooling import OUTPUT_DIR, SITE_DIR, setup_logging
from cluster import _is_front_matter

OPENALEX = "https://api.openalex.org"
# 페이지에서 키워드를 긁을 국내 저널(접근 가능). 해외는 페이월이라 OpenAlex로만.
_HTML_JOURNALS = {"jksarm", "kjas"}


def openalex_keywords(session: requests.Session, doi: str, logger: logging.Logger) -> list[str]:
    """Keywords/concepts for a DOI from OpenAlex (free, no key, bypasses paywalls)."""
    doi = doi.strip().lower().replace("https://doi.org/", "")
    if not doi:
        return []
    mail = os.environ.get("OPENALEX_MAILTO", "")
    url = f"{OPENALEX}/works/doi:{doi}?select=keywords,concepts" + (f"&mailto={mail}" if mail else "")
    try:
        r = session.get(url, timeout=20)
        if r.status_code != 200:
            return []
        data = r.json()
    except (requests.RequestException, ValueError) as exc:  # noqa: BLE001
        logger.info("    OpenAlex 조회 실패 (%s)", type(exc).__name__)
        return []
    kws = [k.get("display_name", "") for k in (data.get("keywords") or [])]
    if not kws:   # fall back to higher-level concepts with decent confidence
        kws = [c.get("display_name", "") for c in (data.get("concepts") or [])
               if c.get("level", 0) >= 1 and (c.get("score") or 0) >= 0.3]
    return clean_keywords(kws)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--journal", default=None, help="한 학회지만 (jksarm/kjas/amarch/archivaria/archrec)")
    p.add_argument("--force", action="store_true", help="이미 키워드가 있어도 다시 가져오기")
    args = p.parse_args(argv)
    logger = setup_logging("fetch_keywords", logfile=False, simple=True)

    files = {f: json.loads(open(f, encoding="utf-8").read())
             for f in sorted(glob.glob(str(OUTPUT_DIR / "*.json")))}
    session = make_session()

    processed: set[str] = set()
    stats: dict[str, list[int]] = {}      # journal -> [found, missing]
    for path, data in files.items():
        jk = data.get("journal_key", "?")
        if args.journal and jk != args.journal:
            continue
        if "articles" not in data:
            continue
        st = stats.setdefault(jk, [0, 0])
        for art in data.get("articles", []):
            if _is_front_matter(art):
                art.pop("topic", None)
                art.pop("topics", None)
                art.pop("keywords", None)
                processed.add(path)
                continue
            if art.get("keywords") and not args.force:
                st[0] += 1
                continue

            kws: list[str] = []
            url = art.get("url", "")
            # 국내 저널만 페이지에서 긁는다. 해외(doi.org→출판사)는 페이월 403이라
            # 재시도만 낭비 → 바로 OpenAlex로.
            page_ok = (jk in _HTML_JOURNALS and url and not url.lower().endswith(".pdf")
                       and "doi.org" not in url.lower())
            if page_ok:
                try:
                    kws = keywords_from_html(fetch_html(session, url, logger))
                except ExtractionError as exc:
                    logger.info("  페이지 조회 실패 (%s): %s", art.get("title", "")[:30], exc)
            if not kws and art.get("doi"):          # 해외·차단 페이지는 OpenAlex로
                kws = openalex_keywords(session, art["doi"], logger)

            if kws:
                art["keywords"] = kws
                art["topics"] = kws
                art["topic"] = kws[0]
                st[0] += 1
                processed.add(path)
                logger.info("  ✓ %s — %s", art.get("title", "")[:28], ", ".join(kws))
            else:
                st[1] += 1

    for path in processed:
        open(path, "w", encoding="utf-8").write(
            json.dumps(files[path], ensure_ascii=False, indent=2) + "\n")

    logger.info("키워드 수집 결과 (학회지: 찾음/못찾음):")
    for jk, (f_, m_) in stats.items():
        logger.info("  %-11s %d / %d", jk, f_, m_)
    build_site.build_site(OUTPUT_DIR, SITE_DIR, logger)
    logger.info("사이트 재생성 완료. 못 찾은 논문은 keywords.py(로컬 LLM)로 메꿀 수 있어요.")
    logger.info("(git add -A && git commit && git push 로 저장)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
