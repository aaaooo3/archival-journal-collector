"""명령줄 도구들이 공유하는 헬퍼 — 경로 상수와 로깅 설정.

watcher / backfill / summarize / translate / enrich_abstracts / fix_kjas_titles
가 똑같이 반복하던 ``ROOT``·``OUTPUT_DIR`` 등 경로와 ``setup_logging`` 을 한곳에 모아
중복을 없앤다. (stdlib 외 의존성이 없어 순환 import 걱정이 없다.)
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"
SITE_DIR = ROOT / "site"
LOG_DIR = ROOT / "logs"
SUMMARY_DIR = OUTPUT_DIR / "summaries"

_FULL_FMT = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
_SIMPLE_FMT = logging.Formatter("%(message)s")


def setup_logging(name: str, *, logfile: bool = True, simple: bool = False) -> logging.Logger:
    """이름 있는 로거를 만든다.

    - ``logfile=True``  : ``logs/watcher.log`` 에 DEBUG 기록(타임스탬프 포함).
    - ``simple=True``   : 콘솔 출력을 ``%(message)s`` 로(타임스탬프 없이 깔끔하게).
    중복 핸들러가 붙지 않도록 이미 설정된 로거는 그대로 반환한다.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    if logger.handlers:
        return logger

    if logfile:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(LOG_DIR / "watcher.log", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(_FULL_FMT)
        logger.addHandler(fh)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(_SIMPLE_FMT if simple else _FULL_FMT)
    logger.addHandler(console)
    return logger
