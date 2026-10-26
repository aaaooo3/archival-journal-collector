#!/usr/bin/env python3
"""논문별 주제어(키워드) 추출 — 무료·로컬(Ollama).

군집(cluster.py)과 달리 논문을 묶지 않고, **각 논문의 제목+초록을 LLM이 읽어**
그 논문만의 한국어 주제어 3~5개를 뽑아 ``articles[].topics`` 에 저장한다. 사이트의
주제 태그(🏷)와 주제 필터가 이 주제어로 채워진다. 군집 라벨보다 논문별로 구체적이다.

사용:
    ollama serve
    python3 keywords.py                   # 전체(캐시 재사용)
    python3 keywords.py --journal kjas    # 한 학회지만
    python3 keywords.py --force           # 캐시 무시하고 다시 뽑기
    python3 keywords.py --max-keywords 4  # 논문당 최대 개수

결과는 멱등적이다(캐시: output/.keyword_cache.json). 논문마다 LLM을 호출하므로
시간이 걸린다 — 키워드는 짧아 번역보다는 빠르다.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import logging
import re

import requests

import build_site
from summarize import DEFAULT_OLLAMA_HOST, ollama_available, ollama_generate
from tooling import OUTPUT_DIR, SITE_DIR, setup_logging
from cluster import _FOREIGN, _is_front_matter, _model_installed

KW_MODEL = "qwen2.5:7b"
MAX_KW = 5

_SYSTEM = ("당신은 기록학·아카이브 분야 학술 논문의 '주제어'(무엇을 다루는 논문인지)를 뽑는 "
           "도우미입니다. 한국어로만, 설명 없이 키워드만 쉼표로 답하세요.")
_PROMPT = (
    "다음 논문의 '주제어'를 한국어로 {n}개 이내로 뽑으세요.\n"
    "주제어 = 논문이 '무엇을 다루는가'(대상·현상·영역·개념).\n"
    "다음은 뽑지 마세요:\n"
    "① 분석·조사 '방법/기법'(예: IPA 분석, 텍스트마이닝, 설문조사, 사례연구, 내용분석, "
    "델파이, 심층면담) — 이건 주제가 아니라 방법입니다.\n"
    "② '기록관리·아카이브·기록학·연구·방안'처럼 분야 전체를 뜻하는 포괄어.\n"
    "예) 제목이 '텍스트마이닝을 통한 구술사 연구 동향 분석'이면 → 구술사, 연구 동향 (O) / "
    "텍스트마이닝, 동향 분석 (X)\n"
    "원문이 영어여도 한국어 주제어로 쓰세요. 쉼표로 구분해 키워드만 출력(설명·번호·문장 금지):\n\n"
    "제목: {title}\n초록: {abstract}"
)

# 주제가 아니라 '방법'인 단어들 — 키워드로 나와도 버린다.
_METHOD_WORDS = {
    "분석", "내용분석", "사례연구", "설문조사", "설문", "면담", "심층면담", "인터뷰",
    "텍스트마이닝", "토픽모델링", "델파이", "델파이기법", "ipa", "ipa분석", "통계분석",
    "빈도분석", "요인분석", "회귀분석", "네트워크분석", "메타분석", "실증분석", "비교분석",
    "문헌연구", "문헌고찰", "방법론", "연구방법",
}


def _clean_keywords(raw: str, max_kw: int) -> list[str]:
    """LLM 응답을 키워드 리스트로 정리(구분자 분리·잡음 제거·외국문자 제외)."""
    text = (raw or "").strip()
    text = re.sub(r"^(키워드|주제어)\s*[:：]\s*", "", text)
    parts = re.split(r"[,，、;；·/\n]+", text)
    out: list[str] = []
    for p in parts:
        p = re.sub(r'^[\s\-\d.)\]"\'`“”‘’]+|[\s"\'`“”‘’]+$', "", p).strip()
        p = p.strip("()[]")
        if not p or len(p) > 20:
            continue
        if _FOREIGN.search(p):          # 일본어 가나·중국어 한자 섞이면 버림
            continue
        norm = p.lower().replace(" ", "")
        if norm in _METHOD_WORDS or p.endswith(("분석", "기법")) or norm.endswith("방법론"):
            continue                    # 분석 방법·기법은 주제가 아니라 제외
        if p in ("기록관리", "아카이브", "기록학", "연구"):  # 너무 포괄적인 건 제외
            continue
        if p not in out:
            out.append(p)
        if len(out) >= max_kw:
            break
    return out


# 프롬프트·정리 규칙이 바뀌면 캐시가 자동으로 무효화되도록 서명을 만든다.
_SIG = hashlib.sha1(
    (_SYSTEM + _PROMPT + "|".join(sorted(_METHOD_WORDS))).encode("utf-8")
).hexdigest()[:8]


def extract_keywords(host: str, model: str, title: str, abstract: str,
                     max_kw: int, logger: logging.Logger) -> list[str] | None:
    prompt = _PROMPT.format(n=max_kw, title=title[:300], abstract=(abstract or "")[:1500])
    try:
        out = ollama_generate(host, model, prompt, system=_SYSTEM, num_predict=60)
    except requests.RequestException as exc:  # noqa: BLE001
        logger.error("  키워드 생성 실패: %s", exc)
        return None
    return _clean_keywords(out, max_kw)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default=KW_MODEL, help=f"주제어 뽑는 LLM (기본 {KW_MODEL})")
    p.add_argument("--ollama-host", default=DEFAULT_OLLAMA_HOST)
    p.add_argument("--max-keywords", type=int, default=MAX_KW, help=f"논문당 최대 주제어 수 (기본 {MAX_KW})")
    p.add_argument("--journal", default=None, help="한 학회지만 (jksarm/kjas/amarch/archivaria/archrec)")
    p.add_argument("--force", action="store_true", help="캐시 무시하고 다시 뽑기")
    args = p.parse_args(argv)
    logger = setup_logging("keywords", logfile=False, simple=True)

    if not ollama_available(args.ollama_host, logger):
        logger.error("Ollama 서버에 연결할 수 없습니다 (%s). 'ollama serve' 후 다시.", args.ollama_host)
        return 2
    if not _model_installed(args.ollama_host, args.model):
        logger.error("모델 '%s' 이(가) 없습니다. 먼저:  ollama pull %s", args.model, args.model)
        return 3

    cache_path = OUTPUT_DIR / ".keyword_cache.json"
    cache: dict[str, list[str]] = {}
    if cache_path.exists() and not args.force:
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            cache = {}

    files = {f: json.loads(open(f, encoding="utf-8").read())
             for f in sorted(glob.glob(str(OUTPUT_DIR / "*.json")))}

    processed: set[str] = set()
    done = reused = skipped = failed = 0
    for path, data in files.items():
        if args.journal and data.get("journal_key") != args.journal:
            continue
        if "articles" not in data:
            continue
        for art in data.get("articles", []):
            if _is_front_matter(art):
                art.pop("topic", None)
                art.pop("topics", None)
                skipped += 1
                processed.add(path)
                continue
            title = art.get("title_ko") or art.get("title", "")
            abstract = art.get("abstract_ko") or art.get("abstract", "")
            text = (title + " " + abstract).strip()
            if not text:
                continue
            key = f"{args.model}:{args.max_keywords}:{_SIG}:" + hashlib.sha1(text.encode("utf-8")).hexdigest()
            if key in cache:
                kws = cache[key]
                reused += 1
            else:
                kws = extract_keywords(args.ollama_host, args.model, title, abstract,
                                       args.max_keywords, logger)
                if kws is None:
                    failed += 1
                    continue
                cache[key] = kws
                done += 1
                if done % 10 == 0:
                    logger.info("  %d편 주제어 추출…", done)
                    cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
            kws = kws or ["기타"]
            art["topics"] = kws
            art["topic"] = kws[0]
            processed.add(path)

    cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    for path in processed:
        open(path, "w", encoding="utf-8").write(
            json.dumps(files[path], ensure_ascii=False, indent=2) + "\n")

    logger.info("주제어 추출 완료 — 신규 %d편, 캐시 재사용 %d편, 비논문 제외 %d편%s",
                done, reused, skipped, (f", 실패 {failed}편" if failed else ""))
    build_site.build_site(OUTPUT_DIR, SITE_DIR, logger)
    logger.info("사이트 재생성 완료. (git add -A && git commit && git push 로 저장)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
