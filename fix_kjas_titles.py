#!/usr/bin/env python3
"""기록학연구(kjas) 논문 제목을 영문 -> 국문으로 교체(이미 수집된 데이터 대상).

KoreaScience의 ``citation_title`` 메타는 영문이라, 과거에 수집된 kjas 논문 제목이
영문으로 저장돼 있을 수 있습니다. 이 도구는 ``output/kjas_*.json`` 의 논문 중 제목이
한글이 아닌 것만 골라, 상세 페이지의 국문 제목(``article-title``)으로 교체합니다.

- 이미 국문인 제목은 건너뜁니다(멱등적). 즉 **여러 번 돌려도 안전**하고, 실행할 때마다
  아직 영문으로 남아 있던 것만 다시 시도합니다(일시적 503으로 실패했던 것 재시도용).
- 상세 페이지 접근이 실패하면 기존 제목을 그대로 둡니다(논문 누락 없음).
- 하나라도 바뀌면 사이트(site/index.html)를 다시 생성합니다.

사용:
    python3 fix_kjas_titles.py            # 영문으로 남은 kjas 제목 재시도
    python3 fix_kjas_titles.py --force    # 국문인 것도 다시 받아 교체
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import re
import sys
import time

from bs4 import BeautifulSoup

import build_site
import sources.kjas as kjas
from sources.base import clean_text, make_session

REQUEST_TIMEOUT = 20
RETRY = 2


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("fix_titles")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(h)
    return logger


def _detail_url(art: dict) -> str:
    m = re.search(r"(JAKO\w+)", art.get("url", ""))
    return f"https://www.koreascience.kr/article/{m.group(1)}.page?lang=ko" if m else ""


def _fetch_korean_title(session, url: str) -> str | None:
    """국문 제목 문자열, 페이지엔 있으나 국문 없으면 '', 접근 실패면 None."""
    for _ in range(RETRY):
        try:
            r = session.get(url, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            if not r.encoding or r.encoding.lower() == "iso-8859-1":
                r.encoding = r.apparent_encoding
            soup = BeautifulSoup(r.text, "lxml")
            for sel in kjas.SEL_TITLE_KO:
                el = soup.select_one(sel)
                if el:
                    t = clean_text(el.get_text(" "))
                    if t and re.search(r"[가-힣]", t):
                        return t
            return ""
        except Exception:
            time.sleep(3)
    return None


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--force", action="store_true", help="이미 국문인 제목도 다시 받아 교체")
    args = p.parse_args(argv)
    logger = setup_logging()

    session = make_session()
    updated = kept = 0
    for path in sorted(glob.glob("output/kjas_*.json")):
        data = json.loads(open(path, encoding="utf-8").read())
        changed = False
        for art in data.get("articles", []):
            if not args.force and re.search(r"[가-힣]", art.get("title", "")):
                continue
            url = _detail_url(art)
            if not url:
                kept += 1
                continue
            title = _fetch_korean_title(session, url)
            time.sleep(2)
            if title:
                logger.info("  o %s -> %s", art["title"][:28], title[:30])
                art["title"] = title
                changed = True
                updated += 1
            else:
                logger.info("  . 유지(접근불가): %s", art["title"][:34])
                kept += 1
        if changed:
            open(path, "w", encoding="utf-8").write(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    logger.info("\n제목 갱신 %d편, 유지 %d편", updated, kept)
    if updated:
        build_site.build_site(build_site.OUTPUT_DIR, build_site.SITE_DIR, logger)
        logger.info("사이트 재생성 완료. (git add -A && git commit && git push 로 저장)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
