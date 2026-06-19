#!/usr/bin/env python3
"""해외 저널 논문의 제목·초록을 한국어로 번역(무료·로컬 Ollama).

The American Archivist / Archivaria / Archives and Records 처럼 영어로 된 논문의
제목과 초록을 로컬 LLM(Ollama)으로 한국어 번역해 저장합니다. 유료 번역 API나 키가
필요 없습니다(이미 요약에 쓰는 Ollama 그대로 사용).

- 제목: 한국어 번역을 ``title_ko`` 에 저장(원문 영어 ``title`` 은 그대로 보존 →
  사이트에서 국문 제목과 영어 제목을 함께 보여줌).
- 초록: 한국어 번역을 ``abstract_ko`` 에 저장(원문 영어 ``abstract`` 보존).
- 이미 번역돼 있거나(멱등적), 제목이 이미 한글이면 건너뜁니다.

사용:
    ollama serve                                  # 먼저 실행
    python3 translate.py                          # 해외 3종 전체 번역
    python3 translate.py --journal archrec        # 한 저널만
    python3 translate.py --force                  # 기존 번역도 다시
    python3 translate.py --model qwen2.5:7b       # 모델 지정

번역은 논문마다 LLM을 호출하므로 시간이 걸립니다(특히 초록). 저널별로 나눠 돌리면 편합니다.
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import re
import sys

import requests

import build_site
from summarize import (DEFAULT_OLLAMA_HOST, DEFAULT_OLLAMA_MODEL,
                       ollama_available, ollama_generate)

# 번역 대상 해외 저널 키.
INTL_KEYS = ["amarch", "archivaria", "archrec"]

_SYSTEM = ("당신은 영어 학술 텍스트를 한국어로 번역하는 전문 번역가입니다. "
           "반드시 한국어(한글)로만 출력하세요. 중국어 한자나 일본어 등 다른 언어를 "
           "절대 섞지 마세요(인명·약어 등 꼭 필요한 고유명사만 로마자 허용). "
           "번역 결과만 출력하고, 원문·설명·따옴표·머리말을 덧붙이지 마세요.")

# 한국어 번역 결과에 나오면 안 되는 다른 문자 스크립트(한자·가나·아랍·키릴·히브리
# ·태국·데바나가리). 섞이면 모델 오류로 보고 재시도한다(라틴 문자는 고유명사용으로 허용).
_CJK = re.compile(
    r"[぀-ヿ㐀-鿿؀-ۿЀ-ӿ֐-׿฀-๿ऀ-ॿ]"
)
_TITLE_PROMPT = (
    "다음 영어 학술 논문 제목을 자연스럽고 간결한 한국어 제목으로 번역하세요. "
    "영어 단어를 그대로 남기지 말고 모두 한국어로 옮기되, 사람 이름·기관명 같은 "
    "고유명사는 통용 표기를 사용하세요. 비유적 표현도 자연스러운 한국어로 풀어 쓰세요:\n\n{text}"
)
_ABSTRACT_PROMPT = ("다음 영어 논문 초록을 한국어로 번역하세요. 학술적 의미를 정확히 "
                    "보존하세요:\n\n{text}")


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("translate")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(h)
    return logger


def _has_hangul(s: str) -> bool:
    return bool(re.search(r"[가-힣]", s or ""))


def _model_installed(host: str, model: str) -> bool:
    """Ollama에 해당 모델이 받아져 있는지(/api/tags) 확인. 확인 불가 시 True(차단 안 함)."""
    try:
        tags = requests.get(f"{host.rstrip('/')}/api/tags", timeout=5).json().get("models", [])
    except Exception:  # noqa: BLE001
        return True
    names = [m.get("name", "") for m in tags]
    return model in names or f"{model}:latest" in names or any(n.startswith(model + ":") for n in names)


def _translate(host, model, prompt_tmpl, text, num_predict, logger) -> str:
    """번역. 결과에 중국어 한자가 섞이면 최대 3회 재시도, 그래도면 빈 문자열."""
    prompt = prompt_tmpl.format(text=text)
    for attempt in range(3):
        try:
            out = ollama_generate(host, model, prompt, system=_SYSTEM, num_predict=num_predict).strip()
        except Exception as exc:  # noqa: BLE001
            logger.info("    번역 실패 (%s)", type(exc).__name__)
            return ""
        if out and not _CJK.search(out):
            return out
        logger.info("    (한자 섞임 → 재시도 %d)", attempt + 1)
    return ""   # 계속 오염되면 포기(원문 유지)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--journal", default="all", help=f"저널 키 (기본 all = {', '.join(INTL_KEYS)})")
    p.add_argument("--model", default=DEFAULT_OLLAMA_MODEL, help=f"Ollama 모델 (기본 {DEFAULT_OLLAMA_MODEL})")
    p.add_argument("--ollama-host", default=DEFAULT_OLLAMA_HOST)
    p.add_argument("--force", action="store_true", help="이미 번역된 것도 다시 번역")
    p.add_argument("--titles-only", action="store_true",
                   help="제목만 (다시) 번역하고 초록은 건드리지 않음 (제목 품질 개선용, 빠름)")
    args = p.parse_args(argv)
    logger = setup_logging()

    keys = INTL_KEYS if args.journal == "all" else [args.journal]
    files: list[str] = []
    for k in keys:
        files += sorted(glob.glob(f"output/{k}_*.json"))
    if not files:
        logger.error("대상 파일이 없습니다 (output/<저널>_*.json).")
        return 1

    if not ollama_available(args.ollama_host, logger):
        logger.error("Ollama 서버에 연결할 수 없습니다 (%s).\n"
                     "  터미널에서 'ollama serve' 실행 후 다시 시도하세요.", args.ollama_host)
        return 2
    if not _model_installed(args.ollama_host, args.model):
        logger.error("모델 '%s' 이(가) 설치돼 있지 않습니다.\n"
                     "  먼저 받으세요:  ollama pull %s\n"
                     "  (받은 뒤 같은 명령을 다시 실행하면 됩니다.)", args.model, args.model)
        return 3

    n_title = n_abstract = 0
    for path in files:
        data = json.loads(open(path, encoding="utf-8").read())
        changed = False
        logger.info("[%s]", path.split("/")[-1])
        for art in data.get("articles", []):
            # 제목: 영어일 때만 번역. --titles-only/--force 면 기존 번역도 다시.
            redo_title = args.force or args.titles_only or not art.get("title_ko")
            if redo_title and not _has_hangul(art.get("title", "")):
                logger.info("  · %s", art.get("title", "")[:50])
                ko = _translate(args.ollama_host, args.model, _TITLE_PROMPT,
                                art["title"], 200, logger)
                if ko:
                    art["title_ko"] = ko
                    changed = True
                    n_title += 1
                    logger.info("    제목→ %s", ko[:40])
            # 초록: 영어 초록이 있고 아직 번역 안 됐을 때 (--titles-only 면 건너뜀).
            ab = art.get("abstract", "")
            if (not args.titles_only and ab and not _has_hangul(ab)
                    and (args.force or not art.get("abstract_ko"))):
                ko = _translate(args.ollama_host, args.model, _ABSTRACT_PROMPT, ab, 1200, logger)
                if ko:
                    art["abstract_ko"] = ko
                    changed = True
                    n_abstract += 1
                    logger.info("    초록→ %d자", len(ko))
        if changed:
            open(path, "w", encoding="utf-8").write(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    logger.info("\n번역 완료: 제목 %d편, 초록 %d편", n_title, n_abstract)
    if n_title or n_abstract:
        build_site.build_site(build_site.OUTPUT_DIR, build_site.SITE_DIR, logger)
        logger.info("사이트 재생성 완료. (git add -A && git commit && git push 로 저장)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
